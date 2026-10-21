"""One-off (Documents/design_consent_v2.md D8): every patient a receptionist
registered before consent v2 has a password the receptionist typed. This puts
each of those Cognito users into RESET_REQUIRED, so their next login answers
PASSWORD_RESET_REQUIRED and they choose a new password via forgot-password
(code sent to their own phone/email).

Needs MIGRATION_DATABASE_URL (reads every clinic's patients) and Cognito
credentials. Dry run unless --apply.

    python -m scripts.force_patient_password_reset            # list who would be reset
    python -m scripts.force_patient_password_reset --apply
"""

import argparse
import asyncio

from sqlalchemy import text

from app.core.cognito import _client, settings
from app.core.db import get_migration_engine


async def main(apply: bool) -> None:
    engine = get_migration_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT pt.patient_id, COALESCE(p.phone, p.email) AS username FROM patients pt "
                    "JOIN profiles p ON p.id = pt.profile_id "
                    "WHERE pt.registered_by IS NOT NULL AND pt.self_registered = FALSE "
                    "AND p.cognito_sub NOT LIKE 'pending-%' AND p.deleted_at IS NULL"
                )
            )
        ).all()
    await engine.dispose()

    reset = skipped = 0
    for patient_id, username in rows:
        try:
            status = _client().admin_get_user(UserPoolId=settings.cognito_user_pool_id, Username=username)["UserStatus"]
        except _client().exceptions.UserNotFoundException:
            print(f"skip  {patient_id}  no Cognito user")
            skipped += 1
            continue
        # Never logged in (new flow: temporary password) or already reset.
        if status in ("FORCE_CHANGE_PASSWORD", "RESET_REQUIRED"):
            print(f"skip  {patient_id}  {status}")
            skipped += 1
            continue
        print(f"{'reset' if apply else 'would reset'}  {patient_id}  {status}")
        if apply:
            _client().admin_reset_user_password(UserPoolId=settings.cognito_user_pool_id, Username=username)
        reset += 1
    print(f"\n{reset} {'reset' if apply else 'to reset'}, {skipped} skipped, {len(rows)} receptionist-registered patients")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually reset (default: dry run)")
    asyncio.run(main(parser.parse_args().apply))
