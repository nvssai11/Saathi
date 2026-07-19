from types import SimpleNamespace
from uuid import uuid4

from api.models import OrderStatusResponse, to_buyer_status_label


def test_buyer_status_label_ok_states_unaffected():
    assert to_buyer_status_label("PENDING") == "Received"
    assert to_buyer_status_label("IN_PRODUCTION") == "In Production"
    assert to_buyer_status_label("CANCELLED") == "Cancelled"


def test_buyer_status_label_closed_with_no_failures_is_delivered():
    assert to_buyer_status_label("CLOSED", sublots_verified=3, sublots_failed=0) == "Delivered"


def test_buyer_status_label_closed_all_failed_is_distinct_from_delivered():
    assert (
        to_buyer_status_label("CLOSED", sublots_verified=0, sublots_failed=1)
        == "Order failed quality check"
    )


def test_buyer_status_label_closed_partial_failure_flags_issues():
    assert (
        to_buyer_status_label("CLOSED", sublots_verified=2, sublots_failed=1)
        == "Delivered — with quality issues"
    )


def _order_row(status: str = "CLOSED") -> dict:
    return {"order_id": 46, "correlation_id": uuid4(), "status": status, "total_qty": 50}


def _sublot(status: str) -> SimpleNamespace:
    return SimpleNamespace(status=status)


def test_order_status_response_all_sublots_failed_reports_distinct_status():
    response = OrderStatusResponse.from_db(_order_row(), [_sublot("FAILED")])

    assert response.status == "Order failed quality check"
    assert response.sublots_verified == 0
    assert response.sublots_failed == 1


def test_order_status_response_partial_failure_reports_issues_label():
    response = OrderStatusResponse.from_db(
        _order_row(), [_sublot("VERIFIED"), _sublot("VERIFIED"), _sublot("FAILED")]
    )

    assert response.status == "Delivered — with quality issues"
    assert response.sublots_verified == 2
    assert response.sublots_failed == 1


def test_order_status_response_all_verified_is_plain_delivered():
    response = OrderStatusResponse.from_db(
        _order_row(), [_sublot("VERIFIED"), _sublot("VERIFIED")]
    )

    assert response.status == "Delivered"
    assert response.sublots_failed == 0
