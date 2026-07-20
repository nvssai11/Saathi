from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from google import genai

from agents.verification.agent import VerificationAgent
from config import settings
from core.allocation.engine import AllocationConfig, AllocationEngine
from core.domain import (
    OrderSpec,
    SubLotDraft,
    SubLotRecord,
    SublotAssignment,
    TrustEvent,
    VerificationCompletionResult,
    VerificationOutput,
)
from core.exceptions import InvalidStateTransitionError, NeedsHumanReviewError, VerificationError
from core.protocols import IAllocationEngine, ISettlementCalculator, ITrustScorer
from core.settlement.calculator import SettlementCalculator, SettlementConfig
from core.trust.scorer import TrustScorer, TrustScorerConfig
from db.repositories.buyer_payment_repository import BuyerPaymentRepository
from db.repositories.notification_repository import NotificationRepository
from db.repositories.order_repository import OrderRepository
from db.repositories.payment_repository import PaymentRepository
from db.repositories.sublot_repository import SublotRepository
from db.repositories.trust_repository import TrustRepository
from db.repositories.verification_repository import VerificationRepository
from db.repositories.workshop_repository import WorkshopRepository
from events.producer import publish_order_placed
from observability import set_correlation_id
from services.messaging.gateway import ConsoleNotificationGateway, NotificationGateway

logger = logging.getLogger(__name__)


class OrderCoordinator:
    def __init__(
        self,
        order_repo: OrderRepository,
        workshop_repo: WorkshopRepository,
        sublot_repo: SublotRepository,
        trust_repo: TrustRepository,
        verification_repo: VerificationRepository,
        payment_repo: PaymentRepository,
        notification_repo: NotificationRepository,
        allocation_engine: IAllocationEngine,
        trust_scorer: ITrustScorer,
        settlement_calculator: ISettlementCalculator,
        verification_agent: VerificationAgent,
        notification_gateway: NotificationGateway,
        buyer_payment_repo: BuyerPaymentRepository,
    ) -> None:
        self._orders = order_repo
        self._workshops = workshop_repo
        self._sublots = sublot_repo
        self._trust = trust_repo
        self._verifications = verification_repo
        self._payments = payment_repo
        self._notifications = notification_repo
        self._engine = allocation_engine
        self._scorer = trust_scorer
        self._settlement = settlement_calculator
        self._agent = verification_agent
        self._gateway = notification_gateway
        self._buyer_payments = buyer_payment_repo

    async def create_advance_payment(self, order_id: int) -> None:
        order_row = await self._orders.get(order_id)
        if order_row is None or order_row["payment_terms"] == "PAY_ON_DELIVERY":
            return

        estimated_total = self._estimate_pre_allocation_total(order_row)
        percentage = (
            Decimal("1.00")
            if order_row["payment_terms"] == "PAY_UPFRONT"
            else settings.settlement_advance_percentage
        )
        amount = (estimated_total * percentage).quantize(Decimal("0.01"))
        await self._buyer_payments.create_advance(order_id, amount)

    @staticmethod
    def _estimate_pre_allocation_total(order_row) -> Decimal:
        base = Decimal(str(order_row["factory_fallback_cost"])) * order_row["total_qty"]
        fee = (base * settings.platform_fee_percentage).quantize(Decimal("0.01"))
        return (base + fee).quantize(Decimal("0.01"))

    async def create_cancellation_refund(self, order_id: int) -> None:
        order_row = await self._orders.get(order_id)
        if order_row is None or order_row["payment_terms"] == "PAY_ON_DELIVERY":
            return

        existing = await self._buyer_payments.get_for_order(order_id)
        paid_advance = next(
            (p for p in existing if p["kind"] == "ADVANCE" and p["status"] == "PAID"), None
        )
        if paid_advance is None:
            return

        await self._buyer_payments.create_refund(order_id, Decimal(str(paid_advance["amount"])))

    async def on_order_placed(self, order_id: int) -> list[SublotAssignment]:
        logger.info("on_order_placed order_id=%d", order_id)

        try:
            await self._orders.transition_status(order_id, "PENDING", "ALLOCATING")
        except InvalidStateTransitionError:
            logger.info("Order %d already past PENDING — skipping allocation", order_id)
            return []

        order_row = await self._orders.get(order_id)
        if order_row is None:
            logger.error("Order %d not found after transition", order_id)
            return []
        set_correlation_id(str(order_row["correlation_id"]))

        order_spec = OrderSpec(
            order_id=order_id,
            total_qty=order_row["total_qty"],
            deadline=order_row["deadline"],
            quality_min=order_row["quality_min"],
            allocation_date=date.today(),
            factory_fallback_cost=Decimal(str(order_row["factory_fallback_cost"])),
            factory_workshop_id=order_row["factory_workshop_id"],
        )

        bids = await self._workshops.list_bids(order_row["product_type"])
        drafts = self._engine.allocate(order_spec, bids)

        for draft in drafts:
            await self._workshops.reserve_capacity(
                draft.workshop_id, order_row["product_type"], draft.qty_assigned
            )

        sublot_ids = await self._sublots.create_batch(drafts)
        logger.info("Allocated order %d → %d sublots: %s", order_id, len(drafts), sublot_ids)

        await self._orders.transition_status(order_id, "ALLOCATING", "ALLOCATED")

        return [
            SublotAssignment(
                sublot_id=sublot_id,
                order_id=order_id,
                workshop_id=draft.workshop_id,
                product_type=order_row["product_type"],
                qty_assigned=draft.qty_assigned,
            )
            for draft, sublot_id in zip(drafts, sublot_ids)
            if draft.workshop_id != order_row["factory_workshop_id"]
        ]

    async def reconcile_stuck_orders(self) -> list[int]:
        """Republish any order still PENDING past the stuck threshold.

        Covers both a lost dual-write publish and a message that reached
        Kafka but never reached a live, stably-assigned consumer (e.g. a
        redeploy mid-rebalance) — either way, republishing is safe because
        on_order_placed's PENDING->ALLOCATING guard makes re-processing a
        no-op for any order that already moved past PENDING on its own.
        """
        stuck = await self._orders.list_stuck_pending(settings.stuck_order_threshold_seconds)
        for row in stuck:
            await publish_order_placed(row["order_id"], str(row["correlation_id"]))
        return [row["order_id"] for row in stuck]

    async def on_sublot_assigned(
        self,
        workshop_id: int,
        order_id: int,
        sublot_id: int,
        product_type: str,
        qty_assigned: int,
    ) -> None:
        logger.info(
            "on_sublot_assigned workshop_id=%d sublot_id=%d", workshop_id, sublot_id
        )
        await self._notifications.create(
            workshop_id=workshop_id,
            order_id=order_id,
            sublot_id=sublot_id,
            product_type=product_type,
            qty_assigned=qty_assigned,
        )

        phone = await self._workshops.get_phone(workshop_id)
        if phone:
            await self._gateway.send(
                phone,
                f"New order on Saathi: {qty_assigned} x {product_type} "
                f"(sub-lot #{sublot_id}). Open the app to start production.",
            )

    async def on_sublot_delivered(
        self, sublot_id: int, order_id: int, delivered_qty: int
    ) -> None:
        logger.info("on_sublot_delivered sublot_id=%d order_id=%d", sublot_id, order_id)

        sublot = await self._sublots.get(sublot_id)
        if sublot is None:
            logger.error("Sublot %d not found", sublot_id)
            return

        await self._sublots.mark_delivered(sublot_id, delivered_qty)

        order_row = await self._orders.get(order_id)
        if order_row:
            set_correlation_id(str(order_row["correlation_id"]))
        if order_row and await self._sublots.mark_capacity_released(sublot_id):
            await self._workshops.release_capacity(
                sublot.workshop_id, order_row["product_type"], sublot.qty_assigned
            )

            shortfall = sublot.qty_assigned - delivered_qty
            if shortfall > 0 and sublot.workshop_id != order_row["factory_workshop_id"]:
                await self._ensure_in_production(order_id)
                await self._backfill_factory_shortfall(
                    order_id, order_row["product_type"], shortfall
                )

        await self._ensure_in_production(order_id)

        await self._maybe_start_verifying(order_id)
        await self._check_terminal_and_settle(order_id)

    async def auto_verify_expired_deliveries(self) -> int:
        sublot_ids = await self._sublots.list_delivered_past_grace(
            settings.verification_auto_approve_grace_seconds
        )
        verified_count = 0
        for sublot_id in sublot_ids:
            sublot = await self._sublots.get(sublot_id)
            if sublot is None or sublot.status != "DELIVERED":
                continue
            order_row = await self._orders.get(sublot.order_id)
            if order_row:
                set_correlation_id(str(order_row["correlation_id"]))
            on_time = (
                self._compute_on_time(sublot.delivered_at, order_row["deadline"])
                if order_row else False
            )
            await self._record_auto_ok_trust_event(sublot_id, sublot.workshop_id, on_time)
            await self._sublots.transition_status(sublot_id, "VERIFIED")
            await self._maybe_start_verifying(sublot.order_id)
            await self._check_terminal_and_settle(sublot.order_id)
            verified_count += 1
        return verified_count

    async def on_production_started(self, sublot_id: int) -> None:
        sublot = await self._sublots.get(sublot_id)
        if sublot is None:
            logger.error("Sublot %d not found", sublot_id)
            return
        order_row = await self._orders.get(sublot.order_id)
        if order_row:
            set_correlation_id(str(order_row["correlation_id"]))
        if await self._sublots.start_production(sublot_id):
            await self._ensure_in_production(sublot.order_id)

    async def on_verification_complete(
        self,
        sublot_id: int,
        order_id: int,
        photo_path: str,
        buyer_note: str | None = None,
    ) -> VerificationCompletionResult:
        logger.info("on_verification_complete sublot_id=%d", sublot_id)
        sublot = await self._sublots.get(sublot_id)
        if sublot is None:
            return VerificationCompletionResult(status="NOT_FOUND", explanation=None)

        try:
            output = await self._agent.verify(
                photo_path, order_id, sublot.workshop_id, sublot_id, buyer_note=buyer_note,
            )
        except NeedsHumanReviewError:
            await self._sublots.transition_status(sublot_id, "NEEDS_HUMAN_REVIEW")
            logger.warning("Sublot %d flagged NEEDS_HUMAN_REVIEW", sublot_id)
            result = VerificationCompletionResult(status="NEEDS_HUMAN_REVIEW", explanation=None)
            await self._check_terminal_and_settle(order_id)
            return result
        except VerificationError as exc:
            logger.error("VerificationError sublot %d: %s", sublot_id, exc)
            await self._sublots.transition_status(sublot_id, "NEEDS_HUMAN_REVIEW")
            result = VerificationCompletionResult(status="NEEDS_HUMAN_REVIEW", explanation=None)
            await self._check_terminal_and_settle(order_id)
            return result

        result = await self._apply_verification_output(sublot, output, photo_path)
        await self._check_terminal_and_settle(order_id)
        return result

    async def _apply_verification_output(
        self,
        sublot: SubLotRecord,
        output: VerificationOutput,
        photo_path: str | None,
    ) -> VerificationCompletionResult:
        explanations = await self._translate_explanation(output.explanation)
        await self._verifications.save(sublot.sublot_id, output, photo_path, explanations)

        low_confidence_defect = (
            output.verdict == "DEFECT"
            and output.confidence < settings.verification_defect_confidence_threshold
        )

        if low_confidence_defect:
            await self._sublots.transition_status(sublot.sublot_id, "NEEDS_HUMAN_REVIEW")
            logger.warning(
                "Sublot %d: DEFECT verdict at confidence %.2f is below the "
                "%.2f auto-apply threshold — flagged for human review instead "
                "of penalizing the workshop automatically",
                sublot.sublot_id, output.confidence, settings.verification_defect_confidence_threshold,
            )
            return VerificationCompletionResult(
                status="NEEDS_HUMAN_REVIEW", explanation=output.explanation, explanations=explanations,
            )

        new_status = "VERIFIED" if output.verdict in ("OK", "SPEC_AMBIGUITY") else "FAILED"
        await self._sublots.transition_status(sublot.sublot_id, new_status)

        order_row = await self._orders.get(sublot.order_id)
        on_time = (
            self._compute_on_time(sublot.delivered_at, order_row["deadline"])
            if order_row else False
        )

        trust_event = TrustEvent(
            workshop_id=sublot.workshop_id,
            sublot_id=sublot.sublot_id,
            on_time=on_time,
            defect_found=output.verdict == "DEFECT",
            fault_party=output.fault_party,
            created_at=datetime.now(tz=timezone.utc),
        )
        await self._trust.append_event(trust_event)

        if output.verdict == "SPEC_AMBIGUITY":
            await self._workshops.increment_spec_disputes(sublot.workshop_id)

        return VerificationCompletionResult(
            status=new_status, explanation=output.explanation, explanations=explanations,
            fault_party=output.fault_party,
        )

    @staticmethod
    def _compute_on_time(delivered_at: datetime | None, deadline: date) -> bool:
        if delivered_at is None:
            return False
        return delivered_at.date() <= deadline

    async def _translate_explanation(self, explanation: str) -> dict[str, str]:
        languages = settings.translation_target_languages
        if not languages:
            return {}
        results = await asyncio.gather(
            *(self._agent.translate_explanation(explanation, code) for code in languages)
        )
        return {code: text for code, text in zip(languages, results) if text}

    async def on_defect_flagged(
        self,
        order_id: int,
        photo_path: str,
        defect_qty: int,
        description: str,
    ) -> VerificationCompletionResult:
        order_row = await self._orders.get(order_id)
        if order_row is None:
            raise InvalidStateTransitionError(f"Order {order_id} not found")
        set_correlation_id(str(order_row["correlation_id"]))

        sublots = await self._sublots.list_for_order(order_id)
        delivered = [s for s in sublots if s.status == "DELIVERED"]
        candidates = delivered or [s for s in sublots if s.status == "VERIFIED"]
        if not candidates:
            raise InvalidStateTransitionError(
                f"Order {order_id} has no delivered or verified sub-lot to flag a defect against"
            )
        target = max(candidates, key=lambda s: s.sublot_id)

        await self._sublots.transition_status(target.sublot_id, "VERIFYING")

        return await self.on_verification_complete(
            sublot_id=target.sublot_id,
            order_id=order_id,
            photo_path=photo_path,
            buyer_note=f"{defect_qty} units — {description}",
        )

    async def retry_verification(
        self,
        sublot_id: int,
        *,
        guidance: str | None = None,
        verdict: VerificationOutput | None = None,
    ) -> VerificationCompletionResult:
        sublot = await self._sublots.get(sublot_id)
        if sublot is None:
            raise InvalidStateTransitionError(f"Sublot {sublot_id} not found")
        if sublot.status not in ("VERIFYING", "NEEDS_HUMAN_REVIEW"):
            raise InvalidStateTransitionError(
                f"Sublot {sublot_id} is {sublot.status} — only a sublot stuck in "
                "VERIFYING or awaiting NEEDS_HUMAN_REVIEW can be retried"
            )

        order_row = await self._orders.get(sublot.order_id)
        if order_row:
            set_correlation_id(str(order_row["correlation_id"]))

        if guidance is not None or verdict is not None:
            return await self._resume_verification(sublot, guidance, verdict)

        photo_path = self._find_defect_photo(sublot_id, sublot.order_id)
        if photo_path is None:
            raise InvalidStateTransitionError(
                f"No defect photo found on disk for sublot {sublot_id} — cannot retry"
            )

        logger.info("Admin retry_verification (fresh) sublot_id=%d photo_path=%s", sublot_id, photo_path)
        return await self.on_verification_complete(
            sublot_id=sublot_id, order_id=sublot.order_id, photo_path=photo_path,
        )

    async def _resume_verification(
        self,
        sublot: SubLotRecord,
        guidance: str | None,
        verdict: VerificationOutput | None,
    ) -> VerificationCompletionResult:
        if not await self._agent.is_resumable(sublot.sublot_id):
            raise InvalidStateTransitionError(
                f"Sublot {sublot.sublot_id} has no paused verification to resume — "
                "retry without guidance/verdict to start a fresh verification instead"
            )

        logger.info(
            "Admin resume_verification sublot_id=%d mode=%s",
            sublot.sublot_id, "verdict" if verdict is not None else "guidance",
        )
        try:
            if verdict is not None:
                output = await self._agent.resume_with_verdict(sublot.sublot_id, verdict)
            else:
                output = await self._agent.resume_with_guidance(sublot.sublot_id, guidance)
        except NeedsHumanReviewError:
            await self._sublots.transition_status(sublot.sublot_id, "NEEDS_HUMAN_REVIEW")
            result = VerificationCompletionResult(status="NEEDS_HUMAN_REVIEW", explanation=None)
            await self._check_terminal_and_settle(sublot.order_id)
            return result
        except VerificationError as exc:
            logger.error("VerificationError (resume) sublot %d: %s", sublot.sublot_id, exc)
            await self._sublots.transition_status(sublot.sublot_id, "NEEDS_HUMAN_REVIEW")
            result = VerificationCompletionResult(status="NEEDS_HUMAN_REVIEW", explanation=None)
            await self._check_terminal_and_settle(sublot.order_id)
            return result

        photo_path = self._find_defect_photo(sublot.sublot_id, sublot.order_id)
        result = await self._apply_verification_output(sublot, output, photo_path)
        await self._check_terminal_and_settle(sublot.order_id)
        return result

    @staticmethod
    def _find_defect_photo(sublot_id: int, order_id: int) -> str | None:
        candidates = [
            Path(settings.upload_directory) / str(sublot_id),
            Path(settings.upload_directory) / "order-defects" / str(order_id),
        ]
        for directory in candidates:
            if not directory.exists():
                continue
            matches = sorted(directory.glob("defect.*"))
            if matches:
                return str(matches[0])
        return None

    async def enforce_deadline(self, order_id: int) -> int:
        order_row = await self._orders.get(order_id)
        if order_row is None:
            raise InvalidStateTransitionError(f"Order {order_id} not found")
        set_correlation_id(str(order_row["correlation_id"]))

        if date.today() <= order_row["deadline"]:
            raise InvalidStateTransitionError(
                f"Order {order_id} deadline ({order_row['deadline']}) has not passed yet"
            )

        sublots = await self._sublots.list_for_order(order_id)
        stuck = [s for s in sublots if s.status in ("ASSIGNED", "IN_PRODUCTION")]

        for s in stuck:
            await self._sublots.transition_status(s.sublot_id, "FAILED")
            await self._workshops.release_capacity(
                s.workshop_id, order_row["product_type"], s.qty_assigned
            )
            logger.warning(
                "Order %d sublot %d marked FAILED by deadline enforcement "
                "(deadline was %s)", order_id, s.sublot_id, order_row["deadline"],
            )
            await self._backfill_factory_shortfall(
                order_id, order_row["product_type"], s.qty_assigned
            )

        if stuck:
            await self._maybe_start_verifying(order_id)
            await self._check_terminal_and_settle(order_id)

        return len(stuck)

    async def _ensure_in_production(self, order_id: int) -> None:
        order = await self._orders.get(order_id)
        if order and order["status"] == "ALLOCATED":
            try:
                await self._orders.transition_status(order_id, "ALLOCATED", "IN_PRODUCTION")
            except InvalidStateTransitionError:
                logger.debug("Order %d already IN_PRODUCTION — concurrent delivery", order_id)

    async def _maybe_start_verifying(self, order_id: int) -> None:
        if not await self._sublots.all_delivered_or_further(order_id):
            return
        try:
            await self._orders.transition_status(order_id, "IN_PRODUCTION", "VERIFYING")
        except InvalidStateTransitionError:
            logger.debug(
                "Order %d not eligible for IN_PRODUCTION->VERIFYING (already past it, "
                "or not yet IN_PRODUCTION)", order_id,
            )

    async def _backfill_factory_shortfall(
        self, order_id: int, product_type: str, shortfall_qty: int
    ) -> None:
        factory = await self._workshops.get_factory(product_type)
        if factory is None:
            logger.error(
                "Order %d: no factory configured for product_type=%s — "
                "cannot backfill shortfall of %d units",
                order_id, product_type, shortfall_qty,
            )
            return

        draft = SubLotDraft(
            order_id=order_id,
            workshop_id=factory["workshop_id"],
            qty_assigned=shortfall_qty,
            cost_per_unit=Decimal(str(factory["cost_per_unit"])),
        )
        sublot_ids = await self._sublots.create_batch([draft])
        new_sublot_id = sublot_ids[0]

        await self._workshops.reserve_capacity(factory["workshop_id"], product_type, shortfall_qty)

        logger.warning(
            "Order %d: shortfall of %d units assigned to factory as sublot %d — "
            "awaiting a real delivery, not auto-completed",
            order_id, shortfall_qty, new_sublot_id,
        )

        try:
            await self._orders.transition_status(order_id, "IN_PRODUCTION", "FACTORY_FALLBACK")
        except InvalidStateTransitionError:
            logger.debug(
                "Order %d not eligible for IN_PRODUCTION->FACTORY_FALLBACK label "
                "(already past IN_PRODUCTION) — backfill sublot still created", order_id,
            )

    async def _check_terminal_and_settle(self, order_id: int) -> None:
        if not await self._sublots.all_terminal(order_id):
            return

        logger.info("All sublots terminal for order %d — running settlement", order_id)
        try:
            await self._orders.transition_status(order_id, "IN_PRODUCTION", "SETTLING")
        except InvalidStateTransitionError:
            try:
                await self._orders.transition_status(order_id, "VERIFYING", "SETTLING")
            except InvalidStateTransitionError:
                try:
                    await self._orders.transition_status(order_id, "FACTORY_FALLBACK", "SETTLING")
                except InvalidStateTransitionError:
                    logger.error(
                        "Order %d could not transition to SETTLING from IN_PRODUCTION, "
                        "VERIFYING, or FACTORY_FALLBACK",
                        order_id,
                    )
                    return

        sublots = await self._sublots.list_for_order(order_id)
        verifications = await self._verifications.get_for_order(order_id)
        result = self._settlement.compute(sublots, verifications)

        sublot_ids = [s.sublot_id for s in sublots]
        await self._payments.save_settlement(order_id, sublot_ids, result)

        order_row = await self._orders.get(order_id)
        if order_row is not None and order_row["payment_terms"] != "PAY_ON_DELIVERY":
            existing = await self._buyer_payments.get_for_order(order_id)
            advance_amount = next(
                (Decimal(str(p["amount"])) for p in existing if p["kind"] == "ADVANCE"),
                Decimal("0.00"),
            )
            balance = self._settlement.compute_balance_due(result.buyer_total, advance_amount)
            await self._buyer_payments.create_balance(order_id, balance)

        await self._orders.transition_status(order_id, "SETTLING", "CLOSED")
        logger.info(
            "Order %d CLOSED. buyer_total=%s platform_fee=%s",
            order_id, result.buyer_total, result.platform_fee,
        )

    async def _record_auto_ok_trust_event(
        self, sublot_id: int, workshop_id: int, on_time: bool
    ) -> None:
        await self._trust.append_event(TrustEvent(
            workshop_id=workshop_id,
            sublot_id=sublot_id,
            on_time=on_time,
            defect_found=False,
            fault_party="none",
            created_at=datetime.now(tz=timezone.utc),
        ))


def build_coordinator(pool, checkpointer) -> OrderCoordinator:
    trust_scorer = TrustScorer(TrustScorerConfig(
        window_size=settings.trust_window_size,
        cold_start_score=settings.trust_cold_start_score,
        recency_decay=settings.trust_recency_decay,
    ))
    order_repo = OrderRepository(pool)
    trust_repo = TrustRepository(pool, trust_scorer)
    verification_repo = VerificationRepository(pool)

    return OrderCoordinator(
        order_repo=order_repo,
        workshop_repo=WorkshopRepository(pool),
        sublot_repo=SublotRepository(pool),
        trust_repo=trust_repo,
        verification_repo=verification_repo,
        payment_repo=PaymentRepository(pool),
        notification_repo=NotificationRepository(pool),
        allocation_engine=AllocationEngine(AllocationConfig(
            trust_minimum_threshold=settings.trust_minimum_threshold,
            trust_penalty_factor=settings.trust_penalty_factor,
            spec_disputes_threshold=settings.spec_disputes_threshold,
            spec_disputes_mip_penalty_factor=settings.spec_disputes_mip_penalty_factor,
            solver_time_limit_seconds=settings.allocation_solver_time_limit_seconds,
        )),
        trust_scorer=trust_scorer,
        settlement_calculator=SettlementCalculator(SettlementConfig(
            platform_fee_percentage=settings.platform_fee_percentage,
            penalty_non_delivery_percentage=settings.penalty_non_delivery_percentage,
        )),
        verification_agent=VerificationAgent(
            client=genai.Client(api_key=settings.gemini_api_key),
            order_repo=order_repo,
            trust_repo=trust_repo,
            verification_repo=verification_repo,
            checkpointer=checkpointer,
        ),
        notification_gateway=ConsoleNotificationGateway(),
        buyer_payment_repo=BuyerPaymentRepository(pool),
    )
