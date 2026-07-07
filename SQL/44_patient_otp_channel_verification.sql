-- Patient signup redesign (Stage 13): email-or-mobile signup with OTP
-- verification of the chosen channel at signup, then a required follow-up
-- verification of the OTHER channel post-signup. Cognito's own user
-- attributes (email_verified/phone_number_verified) are the real source of
-- truth for whether a channel can be used as a login alias, but the app
-- needs to show "verify your email" banners without calling Cognito's
-- GetUser on every request — these two columns mirror that state locally,
-- same pattern as is_active/consent_signed already do for account
-- activation. Not used at all in auth_mode='local'.

ALTER TABLE profiles
    ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN phone_verified BOOLEAN NOT NULL DEFAULT FALSE;
