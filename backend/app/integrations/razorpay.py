"""Razorpay client. Real calls when settings.razorpay_key_id/secret are set
(Stage 10 sandbox keys, provided by the user separately per the dev plan);
stub mode otherwise — orders are created with a synthetic ID and payments
module callers get the same response shape either way, so no code elsewhere
needs to know which mode is active."""

import hashlib
import hmac
import uuid
from decimal import Decimal, ROUND_HALF_UP

from app.config import get_settings

settings = get_settings()


def is_configured() -> bool:
    return bool(settings.razorpay_key_id and settings.razorpay_key_secret)


def _to_paise(amount: float) -> int:
    # str(amount) first — Decimal(float) would inherit the float's binary
    # rounding error (e.g. 19.99 -> 19.9899999999999984...) before we ever
    # get a chance to round it.
    return int((Decimal(str(amount)) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def create_order(*, amount: float, currency: str, receipt: str) -> dict:
    paise = _to_paise(amount)
    if not is_configured():
        return {"id": f"order_stub_{uuid.uuid4().hex[:14]}", "status": "created", "amount": paise, "currency": currency}

    import razorpay  # imported lazily — only required once real keys exist

    client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    # payment_capture=1 — auto-capture on successful authorization. Without
    # it Razorpay leaves the payment merely 'authorized', payment.captured
    # never fires, and the webhook (the only route to 'paid') never sees it.
    return client.order.create({"amount": paise, "currency": currency, "receipt": receipt, "payment_capture": 1})


def verify_webhook_signature(*, payload: bytes, signature: str) -> bool:
    if not is_configured():
        # Stub mode: no real webhook secret to verify against — callers
        # should not be hitting the real webhook path at all in this mode.
        return True
    assert settings.razorpay_webhook_secret is not None, (
        "RAZORPAY_WEBHOOK_SECRET must be set once razorpay_key_id/secret are configured "
        "— it is a separate secret from the API key, set in the Razorpay dashboard's webhook config"
    )
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
        return True
    expected = hmac.new(
        settings.razorpay_key_secret.encode(),
        f"{razorpay_order_id}|{razorpay_payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, razorpay_signature)
