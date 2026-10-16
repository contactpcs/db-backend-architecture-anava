"""Razorpay client. Real calls when settings.razorpay_key_id/secret are set
(Stage 10 sandbox keys, provided by the user separately per the dev plan);
stub mode otherwise — orders are created with a synthetic ID and payments
module callers get the same response shape either way, so no code elsewhere
needs to know which mode is active."""

import hashlib
import hmac
import uuid
from decimal import ROUND_HALF_UP, Decimal

from app.config import get_settings

settings = get_settings()


def is_configured() -> bool:
    return bool(settings.razorpay_key_id and settings.razorpay_key_secret)


def _stub_allowed() -> bool:
    """Signature checks without keys pass only on a developer machine or in
    tests. Anywhere else a missing key must fail closed, never trust the
    caller (audit M1)."""
    return settings.environment in ("local", "test")


def is_gateway_rejection(exc: Exception) -> bool:
    """True when Razorpay itself refused the request (4xx). Network errors,
    timeouts and Razorpay 5xx are temporary and worth retrying (audit H2)."""
    try:
        from razorpay.errors import BadRequestError
    except ImportError:  # SDK absent = stub mode, nothing to reject
        return False
    return isinstance(exc, BadRequestError)


def order_payments(razorpay_order_id: str) -> list[dict]:
    """Every payment attempt Razorpay holds for one order (status captured /
    failed / authorized / ...). The reconciler asks this for orders whose
    confirmation never reached us (audit H1)."""
    if not is_configured():
        return []
    return _client().order.payments(razorpay_order_id).get("items") or []


def _to_paise(amount: "float | Decimal") -> int:
    # str(amount) first — Decimal(float) would inherit the float's binary
    # rounding error (e.g. 19.99 -> 19.9899999999999984...) before we ever
    # get a chance to round it.
    return int((Decimal(str(amount)) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def create_order(*, amount: "float | Decimal", currency: str, receipt: str) -> dict:
    paise = _to_paise(amount)
    if not is_configured():
        return {"id": f"order_stub_{uuid.uuid4().hex[:14]}", "status": "created", "amount": paise, "currency": currency}

    client = _client()  # razorpay imported lazily — only required once real keys exist
    # payment_capture=1 — auto-capture on successful authorization. Without
    # it Razorpay leaves the payment merely 'authorized', payment.captured
    # never fires, and the webhook (the only route to 'paid') never sees it.
    return client.order.create({"amount": paise, "currency": currency, "receipt": receipt, "payment_capture": 1})


def _client():
    import razorpay

    return razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))


def refund_payment(*, razorpay_payment_id: str, amount: float, notes: dict) -> dict:
    """Refund `amount` of a captured payment; returns Razorpay's refund entity
    ({id, status: pending|processed|failed, ...}).

    Idempotent against our own retries: if the gateway already holds a refund
    for this payment (a previous pass created it but our DB write was lost),
    that refund is returned instead of creating a second one."""
    if not is_configured():
        return {"id": f"rfnd_stub_{uuid.uuid4().hex[:14]}", "status": "processed", "amount": _to_paise(amount)}
    client = _client()
    existing = client.payment.fetch_multiple_refund(razorpay_payment_id).get("items") or []
    if existing:
        return existing[0]
    return client.payment.refund(razorpay_payment_id, {"amount": _to_paise(amount), "speed": "normal", "notes": notes})


def fetch_refund(razorpay_refund_id: str) -> dict:
    if not is_configured():
        return {"id": razorpay_refund_id, "status": "processed"}
    return _client().refund.fetch(razorpay_refund_id)


def verify_webhook_signature(*, payload: bytes, signature: str) -> bool:
    if not is_configured():
        return _stub_allowed()
    if not settings.razorpay_webhook_secret:
        # Keys without a webhook secret: nothing to check against, so refuse.
        return False
    expected = hmac.new(settings.razorpay_webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_payment_signature(*, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str) -> bool:
    """Verifies the response Razorpay Checkout hands the *browser* on
    success (order_id/payment_id/signature) — a second, independent
    confirmation path alongside the webhook, signed with key_secret (not
    webhook_secret — different secret, per Razorpay's own docs). Lets the
    frontend confirm a payment even if the webhook is delayed or (in local
    dev) unreachable; both paths are idempotent and converge on the same
    update_status call, so whichever arrives first wins."""
    if not is_configured():
        return _stub_allowed()
    assert settings.razorpay_key_secret is not None
    expected = hmac.new(
        settings.razorpay_key_secret.encode(),
        f"{razorpay_order_id}|{razorpay_payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, razorpay_signature)
