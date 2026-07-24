from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, insert_returning


class NotificationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: dict) -> dict:
        sql, params = insert_returning("notifications", data)
        return await fetch_one(self.session, sql, params)

    async def list_for_recipient(self, recipient_id: UUID, *, unread_only: bool = False) -> list[dict]:
        clause = "recipient_id = :rid" + (" AND is_read = FALSE" if unread_only else "")
        rows = (
            await self.session.execute(
                text(f"SELECT * FROM notifications WHERE {clause} ORDER BY created_at DESC LIMIT 100"),
                {"rid": str(recipient_id)},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def unread_count(self, recipient_id: UUID) -> int:
        row = (
            await self.session.execute(
                text("SELECT COUNT(*) AS n FROM notifications WHERE recipient_id = :rid AND is_read = FALSE"),
                {"rid": str(recipient_id)},
            )
        ).mappings().one()
        return row["n"]

    async def mark_read(self, recipient_id: UUID, notification_ids: list[UUID] | None) -> int:
        if notification_ids:
            result = await self.session.execute(
                text("UPDATE notifications SET is_read = TRUE, read_at = NOW() WHERE recipient_id = :rid AND notification_id = ANY(:ids)"),
                {"rid": str(recipient_id), "ids": [str(i) for i in notification_ids]},
            )
        else:
            result = await self.session.execute(
                text("UPDATE notifications SET is_read = TRUE, read_at = NOW() WHERE recipient_id = :rid AND is_read = FALSE"),
                {"rid": str(recipient_id)},
            )
        return result.rowcount  # type: ignore[attr-defined]  # CursorResult has rowcount; async Result stubs don't expose it
