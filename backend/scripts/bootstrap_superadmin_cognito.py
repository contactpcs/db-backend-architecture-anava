"""One-time: creates the first real super_admin account once Cognito is
live. Run after wipe_all_accounts.py, before anything else — every other
account (regions' regional_admins, clinics' clinic_admins, staff, patients)
gets created through the app afterward by this same super_admin.

Same bootstrap problem seed_dev_profile.py solves for local dev: the first
account has no authenticated admin above it to create it through the normal
staff-onboarding flow, so this connects with the master DB role directly
(get_migration_engine(), bypasses RLS) and calls Cognito's AdminCreateUser
directly too, rather than going through any of our own endpoints.

Uses the SAME provisioning as any other staff account (provision_staff_user
— Cognito auto-generates and emails a temp password) rather than a special
permanent-password path, so the exact same NEW_PASSWORD_REQUIRED first-login
flow every other staff account goes through is exercised here too — one
fewer path to have gotten wrong.

Edit SUPERADMIN_EMAIL/FIRST_NAME/LAST_NAME below before running.
Usage: python -m scripts.bootstrap_superadmin_cognito
"""

import asyncio

from sqlalchemy import text

from app.config import get_settings
from app.core.db import get_migration_engine

SUPERADMIN_EMAIL = "contact@anavaclinics.com"
SUPERADMIN_FIRST_NAME = "Anava"
SUPERADMIN_LAST_NAME = "SuperAdmin"


async def main() -> None:
    settings = get_settings()
    if settings.auth_mode != "cognito":
        raise SystemExit("AUTH_MODE must be 'cognito' — set it in .env before running this.")

    engine = get_migration_engine()
    async with engine.connect() as conn:
        existing = (await conn.execute(text("SELECT 1 FROM profiles WHERE email = :email"), {"email": SUPERADMIN_EMAIL})).first()
    if existing:
        raise SystemExit(f"{SUPERADMIN_EMAIL!r} already exists in this database — nothing to do. "
                          f"(Safe to reuse this script across separate dev/test/prod environments — "
                          f"each has its own DB and its own Cognito pool — but not to re-run twice against the same one.)")

    from app.core.cognito import provision_staff_user

    cognito_sub = provision_staff_user(
        email=SUPERADMIN_EMAIL, first_name=SUPERADMIN_FIRST_NAME, last_name=SUPERADMIN_LAST_NAME, phone=None,
    )

    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "INSERT INTO profiles (cognito_sub, email, first_name, last_name, role, is_active, consent_signed) "
                    "VALUES (:sub, :email, :first_name, :last_name, 'super_admin', TRUE, TRUE) RETURNING id"
                ),
                {"sub": cognito_sub, "email": SUPERADMIN_EMAIL, "first_name": SUPERADMIN_FIRST_NAME, "last_name": SUPERADMIN_LAST_NAME},
            )
        ).mappings().one()
        await conn.execute(
            text("INSERT INTO admins (profile_id, admin_type) VALUES (:pid, 'super_admin')"),
            {"pid": row["id"]},
        )
    await engine.dispose()
    print(f"Created super_admin {SUPERADMIN_EMAIL!r} (cognito_sub={cognito_sub}). "
          f"Cognito emailed a temp password to that address — first login will need /auth/login/new-password.")


if __name__ == "__main__":
    asyncio.run(main())
