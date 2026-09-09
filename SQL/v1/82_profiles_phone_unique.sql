-- Root cause of duplicate phone numbers in core.profiles: no UNIQUE constraint
-- ever existed on the column, and both signup/complete and verify-channel/confirm
-- write the raw phone value with no ownership check. Confirmed live: 5 rows across
-- 2 phone numbers, all from the same person re-registering under different emails
-- and re-verifying the same mobile each time.
--
-- Cleanup keeps the newest profile per duplicated phone (real, currently-verified
-- account) and nulls the older row(s)' phone instead of deleting the profile —
-- no profile/account data is lost.
UPDATE core."profiles" p
SET "phone" = NULL
WHERE p."phone" IS NOT NULL
  AND p."id" NOT IN (
      SELECT DISTINCT ON ("phone") "id"
      FROM core."profiles"
      WHERE "phone" IS NOT NULL
      ORDER BY "phone", "created_at" DESC
  );

-- Partial (not full UNIQUE constraint) because phone is nullable and multiple
-- profiles legitimately have no phone yet (mobile-only signups start with a
-- placeholder email but always have a real phone; email-only signups have
-- phone = NULL until they verify it later via /verify-channel).
CREATE UNIQUE INDEX IF NOT EXISTS "uq_profiles_phone" ON core."profiles" ("phone") WHERE "phone" IS NOT NULL;
