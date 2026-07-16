from __future__ import annotations

from dataclasses import dataclass

from core.domain import TrustEvent


_GRADE_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.85, "A"),
    (0.70, "B"),
    (0.50, "C"),
    (0.0,  "D"),
)

_WEIGHT_ON_TIME = 0.6
_WEIGHT_QUALITY = 0.4


@dataclass(frozen=True)
class TrustScorerConfig:
    window_size: int
    cold_start_score: float


class TrustScorer:
    def __init__(self, config: TrustScorerConfig) -> None:
        self._config = config

    @property
    def window_size(self) -> int:
        return self._config.window_size

    def compute_score(self, events: list[TrustEvent]) -> float:
        if not events:
            return self._config.cold_start_score
        _, on_time_rate, defect_rate = self._window_and_rates(events)
        return self._apply_formula(on_time_rate, defect_rate)

    def compute_defect_rate(self, events: list[TrustEvent]) -> float:
        if not events:
            return 0.0
        _, _, defect_rate = self._window_and_rates(events)
        return round(defect_rate, 4)

    def grade(self, score: float) -> str:
        for threshold, letter in _GRADE_THRESHOLDS:
            if score >= threshold:
                return letter
        return "D"

    def score_explanation(self, events: list[TrustEvent]) -> list[str]:
        if not events:
            return [f"No completed sub-lots yet. Starting score: {self._config.cold_start_score:.3f}"]

        window, on_time_rate, defect_rate = self._window_and_rates(events)
        score = self._apply_formula(on_time_rate, defect_rate)

        return [
            f"Score: {score:.3f} ({self.grade(score)}) over last {len(window)} sub-lots",
            f"On-time delivery rate: {on_time_rate:.1%}",
            f"Workshop-fault defect rate: {defect_rate:.1%}",
        ]

    def _window_and_rates(
        self, events: list[TrustEvent]
    ) -> tuple[list[TrustEvent], float, float]:
        window  = self._recent_window(events)
        weights = self._descending_weights(len(window))

        on_time_rate = self._weighted_mean([e.on_time for e in window], weights)
        defect_rate  = self._weighted_mean(
            [e.defect_found and e.fault_party == "workshop" for e in window],
            weights,
        )
        return window, on_time_rate, defect_rate

    def _apply_formula(self, on_time_rate: float, defect_rate: float) -> float:
        score = _WEIGHT_ON_TIME * on_time_rate + _WEIGHT_QUALITY * (1.0 - defect_rate)
        return round(min(max(score, 0.0), 1.0), 4)

    def _recent_window(self, events: list[TrustEvent]) -> list[TrustEvent]:
        sorted_events = sorted(events, key=lambda e: e.created_at)
        return sorted_events[-self._config.window_size:]

    @staticmethod
    def _descending_weights(n: int) -> list[float]:
        raw = [float(i + 1) for i in range(n)]
        total = sum(raw)
        return [w / total for w in raw]

    @staticmethod
    def _weighted_mean(bools: list[bool], weights: list[float]) -> float:
        return sum(w * (1.0 if v else 0.0) for v, w in zip(bools, weights))
