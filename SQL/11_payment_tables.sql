-- ============================================================
-- Anava Clinic — DB Schema
-- File 11: Payment Tables
-- Depends on: sessions (06), store_orders (10)
-- ============================================================

-- ------------------------------------------------------------
-- payments — Razorpay payment records
--
-- Exactly ONE of session_id or order_id must be set.
-- razorpay_order_id: created by backend, sent to frontend
-- razorpay_payment_id: filled by Razorpay webhook after success
-- idempotency_key: hash of Razorpay webhook event_id — prevents
--   duplicate processing when Razorpay retries webhook delivery.
--   Format: SHA-256 of (razorpay_event_id || payment_type)
-- gateway_response: raw Razorpay webhook payload for audit/reconciliation
-- waived_by: Clinic Admin only — can waive extended session payments
-- ------------------------------------------------------------
CREATE TABLE payments (
    payment_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID        REFERENCES sessions(session_id) ON DELETE RESTRICT,
    order_id            UUID        REFERENCES store_orders(order_id) ON DELETE RESTRICT,
    idempotency_key     TEXT        NOT NULL UNIQUE,
    razorpay_order_id   TEXT        UNIQUE,
    razorpay_payment_id TEXT        UNIQUE,
    amount              NUMERIC(10, 2) NOT NULL CHECK (amount >= 0),
    currency            TEXT        NOT NULL DEFAULT 'INR',
    payment_method      TEXT        CHECK (payment_method IN (
                                        'cash', 'card', 'upi', 'bank_transfer', 'waived'
                                    )),
    status              TEXT        NOT NULL DEFAULT 'pending'
                                        CHECK (status IN (
                                            'pending', 'paid', 'failed', 'refunded', 'waived'
                                        )),
    gateway_response    JSONB       NOT NULL DEFAULT '{}',
    waived_by           UUID        REFERENCES profiles(id) ON DELETE RESTRICT,
    waived_reason       TEXT,
    paid_at             TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_payment_target CHECK (
        (session_id IS NOT NULL AND order_id IS NULL)
        OR (session_id IS NULL AND order_id IS NOT NULL)
    )
);
