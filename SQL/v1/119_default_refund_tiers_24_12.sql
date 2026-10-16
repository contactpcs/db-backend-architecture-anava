-- 119_default_refund_tiers_24_12.sql
--
-- APPLY ORDER: after 118. Data only (reference.cancellation_policy_tiers).
-- Design: Documents/design_appointment_policy_and_refunds.md, D10.
--
-- Platform default refund rule, for both session types:
--     cancelled >= 24 h before the slot   -> 100 %
--     cancelled >= 12 h (and < 24 h)      ->  50 %
--     cancelled  < 12 h                   ->   0 %  (no row: below every tier)
--
-- Replaces 118's placeholder (12 h -> 100 %) only where the default set for a
-- session type is still exactly that one row, so a default a super admin has
-- already edited is never overwritten. Clinic-specific tier sets are never
-- touched — a clinic with its own rows keeps them.

BEGIN;

DELETE FROM reference."cancellation_policy_tiers" t
WHERE t."clinic_id" IS NULL
  AND t."min_hours_before" = 12 AND t."refund_percent" = 100
  AND (SELECT count(*) FROM reference."cancellation_policy_tiers" d
       WHERE d."clinic_id" IS NULL AND d."session_type" = t."session_type") = 1;

INSERT INTO reference."cancellation_policy_tiers" ("clinic_id", "session_type", "min_hours_before", "refund_percent")
SELECT NULL, v.st, v.hrs, v.pct
FROM (VALUES ('appointment', 24, 100), ('appointment', 12, 50),
             ('device_session', 24, 100), ('device_session', 12, 50)) AS v(st, hrs, pct)
WHERE NOT EXISTS (
    SELECT 1 FROM reference."cancellation_policy_tiers" d
    WHERE d."clinic_id" IS NULL AND d."session_type" = v.st
);

COMMIT;

-- VERIFY
-- SELECT session_type, min_hours_before, refund_percent
--   FROM reference.cancellation_policy_tiers WHERE clinic_id IS NULL ORDER BY 1, 2 DESC;
--   -> appointment 24/100, 12/50 ; device_session 24/100, 12/50
