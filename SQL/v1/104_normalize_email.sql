-- 104_normalize_email.sql
--
-- ops.normalize_email(email): the one definition of "the same mailbox", used
-- to stop one person registering several accounts with aliases of a single
-- address (design: Documents/design_signup_auto_approval.md).
--
--   lower-cased and trimmed, always
--   Gmail:  dots in the name are ignored, +tag is dropped, googlemail.com = gmail.com
--   providers known to deliver name+tag@ to name@: +tag is dropped
--   every other domain: left as typed, because there a +tag address can be a
--   different person
--
-- IMMUTABLE on purpose: core.profiles.email_normalized (105) is a stored
-- generated column built from it. If this list ever changes, that column must
-- be rebuilt (drop and re-add it), or old rows keep their old value.
--
-- SCOPE: one function. APPLY ORDER: after 103, before 105.

CREATE OR REPLACE FUNCTION ops.normalize_email(p_email text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $function$
    SELECT CASE
        WHEN e.domain IN ('gmail.com', 'googlemail.com')
            THEN replace(split_part(e.local_part, '+', 1), '.', '') || '@gmail.com'
        WHEN e.domain IN ('outlook.com', 'hotmail.com', 'live.com', 'icloud.com', 'me.com',
                          'proton.me', 'protonmail.com', 'fastmail.com')
            THEN split_part(e.local_part, '+', 1) || '@' || e.domain
        ELSE e.address
    END
    FROM (
        SELECT lower(btrim(p_email)) AS address,
               split_part(lower(btrim(p_email)), '@', 1) AS local_part,
               split_part(lower(btrim(p_email)), '@', 2) AS domain
    ) e
$function$;
