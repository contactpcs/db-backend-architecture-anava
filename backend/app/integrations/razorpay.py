"""Razorpay client. Real calls when settings.razorpay_key_id/secret are set
(Stage 10 sandbox keys, provided by the user separately per the dev plan);
stub mode otherwise — orders are created with a synthetic ID and payments
module callers get the same response shape either way, so no code elsewhere
needs to know which mode is active."""

import hashlib
import hmac
import uuid

from app.config import get_settings

settings = get_settings()


def is_configured() -> bool:
    return bool(settings.razorpay_key_id and settings.razorpay_key_secret)


def create_order(*, amount: float, currency: str, receipt: str) -> dict:
    if not is_configured():
        return {"id": f"order_stub_{uuid.uuid4().hex[:14]}", "status": "created", "amount": int(amount * 100), "currency": currency}

    import razorpay  # imported lazily — only required once real keys exist

    client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    return client.order.create({"amount": int(amount * 100), "currency": currency, "receipt": receipt})


def verify_webhook_signature(*, payload: bytes, signature: str) -> bool:
    if not is_configured():
        # Stub mode: no real webhook secret to verify against — callers
        # should not be hitting the real webhook path at all in this mode.
        return True
    expected = hmac.new(settings.razorpay_key_secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
