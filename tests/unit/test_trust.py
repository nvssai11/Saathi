import pytest
from datetime import datetime, timedelta

from core.trust.scorer import TrustScorer, TrustScorerConfig
from core.domain import TrustEvent

@pytest.fixture
def scorer() -> TrustScorer:
    config = TrustScorerConfig(window_size=10, cold_start_score=0.500)
    return TrustScorer(config)


def _event(
    workshop_id: int = 1,
    sublot_id: int = 1,
    on_time: bool = True,
    defect_found: bool = False,
    fault_party: str = "none",
    offset_days: int = 0,
) -> TrustEvent:
    return TrustEvent(
        workshop_id=workshop_id,
        sublot_id=sublot_id,
        on_time=on_time,
        defect_found=defect_found,
        fault_party=fault_party,
        created_at=datetime(2026, 7, 1) + timedelta(days=offset_days),
    )

def test_no_events_returns_cold_start(scorer: TrustScorer):
    assert scorer.compute_score([]) == 0.500


def test_cold_start_score_configurable():
    config = TrustScorerConfig(window_size=10, cold_start_score=0.600)
    s = TrustScorer(config)
    assert s.compute_score([]) == 0.600

def test_perfect_score_is_1(scorer: TrustScorer):
    events = [
        _event(sublot_id=i, on_time=True, defect_found=False, fault_party="none", offset_days=i)
        for i in range(10)
    ]
    assert scorer.compute_score(events) == 1.0


def test_all_bad_events_score_zero(scorer: TrustScorer):
    events = [
        _event(sublot_id=i, on_time=False, defect_found=True, fault_party="workshop", offset_days=i)
        for i in range(10)
    ]
    assert scorer.compute_score(events) == 0.0


def test_all_good_events_scores_high(scorer: TrustScorer):
    events = [_event(sublot_id=i, offset_days=i) for i in range(5)]
    score = scorer.compute_score(events)
    assert score > 0.90, f"Expected > 0.90, got {score}"

def test_late_delivery_only_lowers_on_time_component(scorer: TrustScorer):
    events = [_event(sublot_id=1, on_time=False, defect_found=False, offset_days=0)]
    assert abs(scorer.compute_score(events) - 0.4) < 0.0001


def test_workshop_defect_only_lowers_quality_component(scorer: TrustScorer):
    events = [_event(sublot_id=1, on_time=True, defect_found=True, fault_party="workshop")]
    assert abs(scorer.compute_score(events) - 0.6) < 0.0001


def test_workshop_defect_lowers_score(scorer: TrustScorer):
    events = [_event(sublot_id=1, defect_found=True, fault_party="workshop")]
    assert scorer.compute_score(events) < 0.70


def test_buyer_fault_does_not_penalise_workshop(scorer: TrustScorer):
    events = [
        _event(sublot_id=1, on_time=True, defect_found=True, fault_party="buyer", offset_days=0),
    ]
    assert scorer.compute_score(events) == 1.0


def test_spec_ambiguity_does_not_penalise_score(scorer: TrustScorer):
    events = [
        _event(sublot_id=1, on_time=True, defect_found=True, fault_party="buyer"),
        _event(sublot_id=2, on_time=True, defect_found=True, fault_party="buyer", offset_days=1),
        _event(sublot_id=3, on_time=True, defect_found=True, fault_party="buyer", offset_days=2),
    ]
    assert scorer.compute_score(events) == 1.0


def test_score_bounded_0_to_1(scorer: TrustScorer):
    events = [
        _event(sublot_id=i, on_time=False, defect_found=True, fault_party="workshop", offset_days=i)
        for i in range(10)
    ]
    score = scorer.compute_score(events)
    assert 0.0 <= score <= 1.0


def test_late_delivery_lowers_score(scorer: TrustScorer):
    good = [_event(sublot_id=i, on_time=True, offset_days=i) for i in range(5)]
    bad  = [_event(sublot_id=i + 5, on_time=False, offset_days=i + 5) for i in range(5)]
    score = scorer.compute_score(good + bad)
    assert score < 0.80

def test_recent_bad_events_hurt_more_than_old_bad_events(scorer: TrustScorer):
    improving = (
        [_event(sublot_id=i, on_time=False, defect_found=True, fault_party="workshop",
                offset_days=i) for i in range(5)]
        + [_event(sublot_id=i + 5, on_time=True, offset_days=i + 5) for i in range(5)]
    )
    declining = (
        [_event(sublot_id=i, on_time=True, offset_days=i) for i in range(5)]
        + [_event(sublot_id=i + 5, on_time=False, defect_found=True, fault_party="workshop",
                  offset_days=i + 5) for i in range(5)]
    )
    assert scorer.compute_score(improving) > scorer.compute_score(declining)

def test_only_last_n_events_considered():
    config = TrustScorerConfig(window_size=3, cold_start_score=0.500)
    scorer = TrustScorer(config)

    bad  = [_event(sublot_id=i, on_time=False, defect_found=True, fault_party="workshop",
                   offset_days=i) for i in range(7)]
    good = [_event(sublot_id=i + 7, on_time=True, offset_days=i + 7) for i in range(3)]
    score = scorer.compute_score(bad + good)
    assert score == 1.0

@pytest.mark.parametrize("score,expected_grade", [
    (1.0,  "A"),
    (0.85, "A"),
    (0.84, "B"),
    (0.70, "B"),
    (0.69, "C"),
    (0.50, "C"),
    (0.49, "D"),
    (0.0,  "D"),
])
def test_grade_thresholds(scorer: TrustScorer, score: float, expected_grade: str):
    assert scorer.grade(score) == expected_grade


def test_grade_below_zero_falls_through_to_d(scorer: TrustScorer):
    assert scorer.grade(-0.5) == "D"

def test_compute_defect_rate_no_events_returns_zero(scorer: TrustScorer):
    assert scorer.compute_defect_rate([]) == 0.0


def test_compute_defect_rate_all_workshop_defects_is_one(scorer: TrustScorer):
    events = [
        _event(sublot_id=i, defect_found=True, fault_party="workshop", offset_days=i)
        for i in range(3)
    ]
    assert scorer.compute_defect_rate(events) == 1.0


def test_compute_defect_rate_buyer_fault_does_not_count(scorer: TrustScorer):
    events = [
        _event(sublot_id=i, defect_found=True, fault_party="buyer", offset_days=i)
        for i in range(3)
    ]
    assert scorer.compute_defect_rate(events) == 0.0


def test_compute_defect_rate_matches_quality_component_of_compute_score(scorer: TrustScorer):
    events = [
        _event(sublot_id=1, on_time=True, defect_found=True, fault_party="workshop", offset_days=0),
        _event(sublot_id=2, on_time=True, defect_found=False, offset_days=1),
    ]
    score = scorer.compute_score(events)
    defect_rate = scorer.compute_defect_rate(events)
    assert abs(score - (0.6 + 0.4 * (1 - defect_rate))) < 1e-3

def test_compute_on_time_rate_no_events_returns_zero(scorer: TrustScorer):
    assert scorer.compute_on_time_rate([]) == 0.0


def test_compute_on_time_rate_all_on_time_is_one(scorer: TrustScorer):
    events = [_event(sublot_id=i, on_time=True, offset_days=i) for i in range(3)]
    assert scorer.compute_on_time_rate(events) == 1.0


def test_compute_on_time_rate_matches_explanation_line():
    scorer = TrustScorer(TrustScorerConfig(window_size=10, cold_start_score=0.5))
    events = [
        _event(sublot_id=1, on_time=True, offset_days=0),
        _event(sublot_id=2, on_time=False, offset_days=1),
        _event(sublot_id=3, on_time=True, offset_days=2),
    ]
    on_time_rate = scorer.compute_on_time_rate(events)
    explanation_line = scorer.score_explanation(events)[1]
    assert f"{on_time_rate:.1%}" == explanation_line.split(": ")[1]


def test_window_count_no_events_is_zero(scorer: TrustScorer):
    assert scorer.window_count([]) == 0


def test_window_count_caps_at_window_size():
    scorer = TrustScorer(TrustScorerConfig(window_size=3, cold_start_score=0.5))
    events = [_event(sublot_id=i, offset_days=i) for i in range(10)]
    assert scorer.window_count(events) == 3


def test_window_count_fewer_events_than_window_size():
    scorer = TrustScorer(TrustScorerConfig(window_size=10, cold_start_score=0.5))
    events = [_event(sublot_id=i, offset_days=i) for i in range(4)]
    assert scorer.window_count(events) == 4


def test_explanation_no_events(scorer: TrustScorer):
    lines = scorer.score_explanation([])
    assert len(lines) == 1
    assert "No completed" in lines[0]


def test_explanation_has_three_lines(scorer: TrustScorer):
    events = [_event(sublot_id=i, offset_days=i) for i in range(3)]
    lines = scorer.score_explanation(events)
    assert len(lines) == 3


def test_explanation_mentions_grade(scorer: TrustScorer):
    events = [_event(sublot_id=i, offset_days=i) for i in range(5)]
    lines = scorer.score_explanation(events)
    assert any(c in lines[0] for c in ("A", "B", "C", "D"))


def test_explanation_score_matches_compute_score(scorer: TrustScorer):
    events = [
        _event(sublot_id=1, on_time=True,  defect_found=False, offset_days=0),
        _event(sublot_id=2, on_time=False, defect_found=True,  fault_party="workshop", offset_days=1),
        _event(sublot_id=3, on_time=True,  defect_found=True,  fault_party="buyer", offset_days=2),
    ]
    score = scorer.compute_score(events)
    lines = scorer.score_explanation(events)
    score_in_explanation = float(lines[0].split()[1])
    assert abs(score - score_in_explanation) < 0.001


def test_window_size_property_matches_config():
    config = TrustScorerConfig(window_size=7, cold_start_score=0.500)
    scorer = TrustScorer(config)
    assert scorer.window_size == 7
