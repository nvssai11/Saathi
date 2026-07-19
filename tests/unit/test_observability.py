from __future__ import annotations

import logging

from observability import CorrelationIdFilter, get_correlation_id, set_correlation_id


def test_get_correlation_id_defaults_to_none():
    set_correlation_id(None)
    assert get_correlation_id() is None


def test_set_then_get_round_trips():
    set_correlation_id("abc-123")
    assert get_correlation_id() == "abc-123"


def test_filter_attaches_correlation_id_to_log_record():
    set_correlation_id("corr-xyz")
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )

    result = CorrelationIdFilter().filter(record)

    assert result is True
    assert record.correlation_id == "corr-xyz"


def test_filter_uses_dash_placeholder_when_unset():
    set_correlation_id(None)
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )

    CorrelationIdFilter().filter(record)

    assert record.correlation_id == "-"
