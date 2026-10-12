"""One-time: creates the first real super_admin account once Cognito is
live. Run after the schema is built (scripts/apply_sql_v1.py), before anything
else — every other account (regions' regional_admins, clinics' clinic_admins,
staff, patients) gets created through the app afterward by this same super_admin.

Same bootstrap problem seed_dev_profile.py solves for local dev: the first
account has no authenticated admin above it to create it through the normal
staff-onboarding flow, so this connects with the master DB role directly
(get_migration_engine(), bypasses RLS) and calls Cognito's AdminCreateUser
directly too, rather than going through any of our own endpoints.

Uses the SAME provisioning as any other staff account (provision_staff_user
— Cognito auto-generates and emails a temp password) and the SAME activation
path: the profile starts inactive with a PENDING staff_onboarding consent
record, and the super admin signs it on first login. (ops.auth_context
re-checks every request that an active staff member has a signed
staff_onboarding record, so an account created already-active with no
record is switched back to inactive on its first request.)

Safe to run again: an existing Cognito user is reused, an existing profile is
kept, and a missing consent record is added.

The email and name default to the constants below; SUPERADMIN_EMAIL,
SUPERADMIN_FIRST_NAME and SUPERADMIN_LAST_NAME in the environment override
them (handy for a one-off ECS task, where editing this file means a rebuild).
Usage: python -m scripts.bootstrap_superadmin_cognito
"""

import asyncio
import os

from sqlalchemy import text

from app.config import get_settings
from app.core.db import get_migration_engine

SUPERADMIN_EMAIL = os.environ.get("SUPERADMIN_EMAIL", "contact@anavaclinics.com")
SUPERADMIN_FIRST_NAME = os.environ.get("SUPERADMIN_FIRST_NAME", "Anava")
SUPERADMIN_LAST_NAME = os.environ.get("SUPERADMIN_LAST_NAME", "SuperAdmin")


async def main() -> None:
    settings = get_settings()
    if settings.auth_mode != "cognito":
        raise SystemExit("AUTH_MODE must be 'cognito' — set it in .env before running this.")

    from app.core.cognito import get_user_sub, provision_staff_user

    engine = get_migration_engine()
    try:
        async with engine.connect() as conn:
            template = (
                await conn.execute(
                    text(
                        "SELECT template_id FROM reference.consent_templates "
                        "WHERE consent_type = 'staff_onboarding' AND role = 'super_admin' AND is_active "
                        "ORDER BY version DESC LIMIT 1"
                    )
                )
            ).scalar()
            profile = (
                await conn.execute(text("SELECT id, cognito_sub FROM profiles WHERE email = :email"), {"email": SUPERADMIN_EMAIL})
            ).first()
        if template is None:
            raise SystemExit(
                "No active staff_onboarding consent template for super_admin in reference.consent_templates — "
                "load the consent templates first (SQL/v1/112_consent_templates_seed.sql)."
            )

        if profile:
            profile_id, cognito_sub = profile.id, profile.cognito_sub
            print(f"Profile for {SUPERADMIN_EMAIL!r} already exists — keeping it.")
        else:
            cognito_sub = get_user_sub(SUPERADMIN_EMAIL)
            if cognito_sub:
                print(f"Cognito user {SUPERADMIN_EMAIL!r} already exists — reusing it (no new temp password is sent).")
            else:
                cognito_sub = provision_staff_user(
                    email=SUPERADMIN_EMAIL, first_name=SUPERADMIN_FIRST_NAME, last_name=SUPERADMIN_LAST_NAME, phone=None
                )
                print(f"Created Cognito user {SUPERADMIN_EMAIL!r}; Cognito emailed a temp password to that address.")

        async with engine.begin() as conn:
            if not profile:
                profile_id = (
                    await conn.execute(
                        text(
                            "INSERT INTO profiles (cognito_sub, email, first_name, last_name, role, is_active, consent_signed) "
                            "VALUES (:sub, :email, :first_name, :last_name, 'super_admin', FALSE, FALSE) RETURNING id"
                        ),
                        {
                            "sub": cognito_sub,
                            "email": SUPERADMIN_EMAIL,
                            "first_name": SUPERADMIN_FIRST_NAME,
                            "last_name": SUPERADMIN_LAST_NAME,
                        },
                    )
                ).scalar_one()
                await conn.execute(text("INSERT INTO admins (profile_id, admin_type) VALUES (:pid, 'super_admin')"), {"pid": profile_id})
            created = (
                await conn.execute(
                    text(
                        "INSERT INTO compliance.consent_records (consent_type, template_id, staff_id, status) "
                        "SELECT 'staff_onboarding', :template, :pid, 'pending' "
                        "WHERE NOT EXISTS (SELECT 1 FROM compliance.consent_records "
                        "                  WHERE staff_id = :pid AND consent_type = 'staff_onboarding') "
                        "RETURNING consent_id"
                    ),
                    {"template": template, "pid": profile_id},
                )
            ).first()
    finally:
        await engine.dispose()

    print(
        f"Super admin {SUPERADMIN_EMAIL!r} (cognito_sub={cognito_sub}): "
        f"{'pending consent record created' if created else 'consent record already present'}. "
        f"First login needs /auth/login/new-password, then sign the consent form to activate the account."
    )


if __name__ == "__main__":
    asyncio.run(main())
