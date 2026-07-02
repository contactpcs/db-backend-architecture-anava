-- ============================================================
-- Anava Clinic — DB Schema
-- File 12b: Notifications Table
--
-- User-facing in-app notifications. NOT an audit log.
-- is_read toggled by recipient; nothing else updated by recipient.
-- sender_id NULL = system-generated notification.
-- clinic_id NULL = system-wide / cross-clinic notification.
-- expires_at: notification auto-hides after this timestamp.
-- delivery_channel: how the notification is sent to user.
-- delivered_at: timestamp when delivery confirmed.
-- delivery_attempts: retry counter for failed deliveries.
--
-- Types:
--   appointment   — scheduled/cancelled/reminder
--   clinical      — PRS result ready, treatment plan issued
--   store         — order status changes, device collection
--   admin         — staff request approval/rejection, clinic status
--   consent       — consent form requires signature
--   system        — maintenance, announcements
-- ============================================================
CREATE TABLE notifications (
    notification_id  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient_id     UUID        NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    sender_id        UUID        REFERENCES profiles(id) ON DELETE SET NULL,
    clinic_id        UUID        REFERENCES clinics(clinic_id) ON DELETE SET NULL,
    type             TEXT        NOT NULL DEFAULT 'system'
                                     CHECK (type IN (
                                         'appointment',
                                         'clinical',
                                         'store',
                                         'admin',
                                         'consent',
                                         'system'
                                     )),
    delivery_channel TEXT        NOT NULL DEFAULT 'in_app'
                                     CHECK (delivery_channel IN (
                                         'in_app', 'email', 'sms', 'push'
                                     )),
    title            TEXT        NOT NULL,
    body             TEXT,
    -- link-back to the relevant record (e.g. session_id, order_id, request_id)
    entity_type      TEXT,
    entity_id        UUID,
    metadata         JSONB       NOT NULL DEFAULT '{}',
    is_read          BOOLEAN     NOT NULL DEFAULT FALSE,
    read_at          TIMESTAMPTZ,
    delivered_at     TIMESTAMPTZ,
    delivery_attempts INTEGER    NOT NULL DEFAULT 0,
    expires_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
