"""Dev-only: seeds one super_admin profile so local auth+RLS can be verified
end-to-end without a Cognito account. Idempotent (ON CONFLICT DO NOTHING).

Usage: python -m scripts.seed_dev_profile
Then: POST /api/v1/auth/local-login {"cognito_sub": "dev-super-admin"}
"""

import asyncio

from sqlalchemy import text

from app.core.db import engine

DEV_COGNITO_SUB = "dev-super-admin"
DEV_EMAIL = "dev-super-admin@example.local"


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO profiles (cognito_sub, email, first_name, last_name, role)
                VALUES (:sub, :email, 'Dev', 'SuperAdmin', 'super_admin')
                ON CONFLICT (cognito_sub) DO NOTHING
                """
            ),
            {"sub": DEV_COGNITO_SUB, "email": DEV_EMAIL},
        )
        await conn.execute(
            text(
                """
                INSERT INTO admins (profile_id, admin_type)
                SELECT id, 'super_admin' FROM profiles WHERE cognito_sub = :sub
                ON CONFLICT (profile_id) DO NOTHING
                """
            ),
            {"sub": DEV_COGNITO_SUB},
        )
    print(f"Seeded dev super_admin profile (cognito_sub={DEV_COGNITO_SUB!r})")


if __name__ == "__main__":
    asyncio.run(main())
