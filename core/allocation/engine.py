from __future__ import annotations

import logging
from dataclasses import dataclass

import pulp

from core.domain import OrderSpec, SubLotDraft, WorkshopBid
from core.exceptions import AllocationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AllocationConfig:
    trust_minimum_threshold: float
    trust_penalty_factor: float
    spec_disputes_threshold: int
    spec_disputes_mip_penalty_factor: float
    solver_time_limit_seconds: int = 30


@dataclass(frozen=True)
class _ExclusionReason:
    workshop_id: int
    reason: str


class AllocationEngine:
    def __init__(self, config: AllocationConfig) -> None:
        self._config = config

    def allocate(self, order: OrderSpec, bids: list[WorkshopBid]) -> list[SubLotDraft]:
        non_factory_bids = [b for b in bids if b.workshop_id != order.factory_workshop_id]
        if len(non_factory_bids) < len(bids):
            logger.warning(
                "order_id=%d: %d factory bid(s) stripped from input — "
                "factory is modelled as x_factory, not a workshop variable",
                order.order_id, len(bids) - len(non_factory_bids),
            )

        eligible, exclusions = self._filter_eligible(order, non_factory_bids)
        self._log_exclusions(exclusions)

        if not eligible:
            logger.warning(
                "order_id=%d: zero eligible workshops after pre-filter — "
                "routing full qty=%d to factory",
                order.order_id, order.total_qty,
            )
            return self._factory_fallback(order, order.total_qty)

        eligible_capacity = sum(bid.effective_qty for bid in eligible)
        if eligible_capacity < order.total_qty:
            logger.warning(
                "order_id=%d: eligible workshops can only cover %d of %d units — "
                "policy is no partial split, routing full qty to factory",
                order.order_id, eligible_capacity, order.total_qty,
            )
            return self._factory_fallback(order, order.total_qty)

        return self._solve_mip(order, eligible)

    def _filter_eligible(
        self, order: OrderSpec, bids: list[WorkshopBid]
    ) -> tuple[list[WorkshopBid], list[_ExclusionReason]]:
        deadline_days = (order.deadline - order.allocation_date).days
        eligible: list[WorkshopBid] = []
        excluded: list[_ExclusionReason] = []

        for bid in bids:
            reasons = self._exclusion_reasons(bid, order.quality_min, deadline_days)
            if reasons:
                excluded.append(_ExclusionReason(bid.workshop_id, "; ".join(reasons)))
            else:
                eligible.append(bid)

        return eligible, excluded

    def _exclusion_reasons(
        self, bid: WorkshopBid, quality_min: int, deadline_days: int
    ) -> list[str]:
        reasons: list[str] = []
        if bid.trust_score < self._config.trust_minimum_threshold:
            reasons.append(
                f"trust_score={bid.trust_score:.3f} < "
                f"threshold={self._config.trust_minimum_threshold}"
            )
        if bid.quality_tier < quality_min:
            reasons.append(f"quality_tier={bid.quality_tier} < required={quality_min}")
        if bid.lead_time_days > deadline_days:
            reasons.append(f"lead_time={bid.lead_time_days}d > deadline={deadline_days}d")
        if bid.effective_qty <= 0:
            reasons.append(
                f"effective_qty={bid.effective_qty} "
                f"(available={bid.available_qty}, reserved={bid.reserved_qty})"
            )
        return reasons

    def _solve_mip(
        self, order: OrderSpec, eligible: list[WorkshopBid]
    ) -> list[SubLotDraft]:
        problem, workshop_vars, factory_var = self._build_lp(order, eligible)
        time_limit = self._config.solver_time_limit_seconds
        try:
            problem.solve(pulp.COIN_CMD(msg=False, timeLimit=time_limit))
        except pulp.PulpSolverError:
            try:
                problem.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit))
            except pulp.PulpSolverError as exc:
                logger.error(
                    "order_id=%d: all solvers failed (%s) — routing full qty to factory",
                    order.order_id, exc,
                )
                return self._factory_fallback(order, order.total_qty)

        status = pulp.LpStatus.get(problem.status, "Unknown")
        if status != "Optimal":
            logger.error(
                "order_id=%d: MIP solver returned status=%s — routing to factory",
                order.order_id, status,
            )
            return self._factory_fallback(order, order.total_qty)

        return self._extract_assignments(order, eligible, workshop_vars, factory_var)

    def _build_lp(
        self, order: OrderSpec, eligible: list[WorkshopBid]
    ) -> tuple[pulp.LpProblem, dict[int, pulp.LpVariable], pulp.LpVariable]:
        problem = pulp.LpProblem(f"saathi_order_{order.order_id}", pulp.LpMinimize)

        workshop_vars: dict[int, pulp.LpVariable] = {
            bid.workshop_id: pulp.LpVariable(
                f"x_{bid.workshop_id}", lowBound=0, cat=pulp.LpInteger
            )
            for bid in eligible
        }
        factory_var = pulp.LpVariable("x_factory", lowBound=0, cat=pulp.LpInteger)

        problem += (
            pulp.lpSum(
                workshop_vars[bid.workshop_id] * self._adjusted_cost(bid)
                for bid in eligible
            )
            + factory_var * float(order.factory_fallback_cost),
            "total_adjusted_cost",
        )

        problem += (
            pulp.lpSum(workshop_vars[bid.workshop_id] for bid in eligible) + factory_var
            == order.total_qty,
            "demand_satisfaction",
        )

        for bid in eligible:
            problem += (
                workshop_vars[bid.workshop_id] <= bid.effective_qty,
                f"capacity_ws_{bid.workshop_id}",
            )

        return problem, workshop_vars, factory_var

    def _extract_assignments(
        self,
        order: OrderSpec,
        eligible: list[WorkshopBid],
        workshop_vars: dict[int, pulp.LpVariable],
        factory_var: pulp.LpVariable,
    ) -> list[SubLotDraft]:
        drafts: list[SubLotDraft] = []

        for bid in eligible:
            raw = pulp.value(workshop_vars[bid.workshop_id])
            qty = int(round(raw)) if raw is not None else 0
            if qty > 0:
                drafts.append(SubLotDraft(
                    order_id=order.order_id,
                    workshop_id=bid.workshop_id,
                    qty_assigned=qty,
                    cost_per_unit=bid.cost_per_unit,
                ))

        factory_raw = pulp.value(factory_var)
        factory_qty = int(round(factory_raw)) if factory_raw is not None else 0
        if factory_qty > 0:
            drafts.extend(self._factory_fallback(order, factory_qty))

        self._correct_rounding_drift(order, drafts)
        return drafts

    def _correct_rounding_drift(self, order: OrderSpec, drafts: list[SubLotDraft]) -> None:
        total = sum(d.qty_assigned for d in drafts)
        delta = order.total_qty - total
        if delta == 0:
            return

        if abs(delta) > 1:
            logger.error(
                "order_id=%d: MIP rounding drift of %+d units exceeds expected ±1 — "
                "solver may have returned a fractional or infeasible solution",
                order.order_id, delta,
            )
        else:
            logger.warning(
                "order_id=%d: MIP rounding drift of %+d units — correcting in-place",
                order.order_id, delta,
            )

        if delta > 0:
            factory_idx = next(
                (i for i, d in enumerate(drafts) if d.workshop_id == order.factory_workshop_id),
                None,
            )
            if factory_idx is not None:
                old = drafts[factory_idx]
                drafts[factory_idx] = SubLotDraft(
                    order_id=old.order_id,
                    workshop_id=old.workshop_id,
                    qty_assigned=old.qty_assigned + delta,
                    cost_per_unit=old.cost_per_unit,
                )
            else:
                drafts.extend(self._factory_fallback(order, delta))

        else:
            non_factory_indices = [i for i, d in enumerate(drafts) if d.workshop_id != order.factory_workshop_id]
            factory_indices = [i for i, d in enumerate(drafts) if d.workshop_id == order.factory_workshop_id]
            ordered_indices = (
                sorted(non_factory_indices, key=lambda i: drafts[i].qty_assigned, reverse=True)
                + sorted(factory_indices, key=lambda i: drafts[i].qty_assigned, reverse=True)
            )

            remaining = -delta
            to_pop: set[int] = set()
            for i in ordered_indices:
                if remaining <= 0:
                    break
                d = drafts[i]
                take = min(d.qty_assigned, remaining)
                remaining -= take
                new_qty = d.qty_assigned - take
                if new_qty > 0:
                    drafts[i] = SubLotDraft(
                        order_id=d.order_id,
                        workshop_id=d.workshop_id,
                        qty_assigned=new_qty,
                        cost_per_unit=d.cost_per_unit,
                    )
                else:
                    to_pop.add(i)

            if to_pop:
                drafts[:] = [d for i, d in enumerate(drafts) if i not in to_pop]

        post_total = sum(d.qty_assigned for d in drafts)
        if post_total != order.total_qty:
            logger.critical(
                "order_id=%d: demand invariant violated after rounding correction — "
                "expected %d, got %d (original drift %+d)",
                order.order_id, order.total_qty, post_total, delta,
            )

    def _adjusted_cost(self, bid: WorkshopBid) -> float:
        base_multiplier = 1.0 + (1.0 - bid.trust_score) * self._config.trust_penalty_factor

        if bid.spec_disputes >= self._config.spec_disputes_threshold:
            base_multiplier *= (1.0 + self._config.spec_disputes_mip_penalty_factor)

        return float(bid.cost_per_unit) * base_multiplier

    def _factory_fallback(self, order: OrderSpec, qty: int) -> list[SubLotDraft]:
        if order.factory_workshop_id <= 0:
            raise AllocationError(
                f"order_id={order.order_id}: solver failed and no factory_workshop_id "
                "is configured — cannot fulfil order"
            )
        return [SubLotDraft(
            order_id=order.order_id,
            workshop_id=order.factory_workshop_id,
            qty_assigned=qty,
            cost_per_unit=order.factory_fallback_cost,
        )]

    @staticmethod
    def _log_exclusions(exclusions: list[_ExclusionReason]) -> None:
        for ex in exclusions:
            logger.debug(
                "pre-filter excluded workshop_id=%d: %s", ex.workshop_id, ex.reason
            )
