"""webhook_status_for_event / extract_failure_info (payments/service.py) are
the decision logic 66_payment_logs.sql's enforcement depends on — which
Razorpay webhook events actually change payments.status vs. get logged
without touching it, and where a failure's code/reason come from."""

from app.modules.payments.service import extract_failure_info, webhook_status_for_event


def test_captured_and_order_paid_both_resolve_to_paid():
    assert webhook_status_for_event("payment.captured") == "paid"
    assert webhook_status_for_event("order.paid") == "paid"


def test_payment_failed_resolves_to_failed():
    assert webhook_status_for_event("payment.failed") == "failed"


def test_unrecognized_or_informational_events_do_not_change_status():
    for event in ("payment.authorized", "refund.processed", "refund.created", "", "something.new"):
        assert webhook_status_for_event(event) is None


def test_extract_failure_info_reads_error_code_and_description():
    body = {"payload": {"payment": {"entity": {"error_code": "BAD_REQUEST_ERROR", "error_description": "Card declined"}}}}
    assert extract_failure_info(body) == ("BAD_REQUEST_ERROR", "Card declined")


def test_extract_failure_info_missing_fields_is_none_none():
    assert extract_failure_info({}) == (None, None)
    assert extract_failure_info({"payload": {"payment": {"entity": {}}}}) == (None, None)
