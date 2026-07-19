from typing import Protocol, runtime_checkable

from core.domain import (
    OrderSpec,
    WorkshopBid,
    SubLotDraft,
    TrustEvent,
    SubLotRecord,
    VerificationRecord,
    SettlementResult,
)


@runtime_checkable
class IAllocationEngine(Protocol):
    def allocate(self, order: OrderSpec, bids: list[WorkshopBid]) -> list[SubLotDraft]:
        ...


@runtime_checkable
class ITrustScorer(Protocol):
    def compute_score(self, events: list[TrustEvent]) -> float:
        ...

    def grade(self, score: float) -> str:
        ...

    def score_explanation(self, events: list[TrustEvent]) -> list[str]:
        ...

    def compute_on_time_rate(self, events: list[TrustEvent]) -> float:
        ...

    def window_count(self, events: list[TrustEvent]) -> int:
        ...


@runtime_checkable
class ISettlementCalculator(Protocol):
    def compute(
        self,
        sublots: list[SubLotRecord],
        verifications: dict[int, VerificationRecord],
    ) -> SettlementResult:
        ...
