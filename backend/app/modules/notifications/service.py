from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.repository import NotificationRepository


class NotificationService:
    def __init__(self, session: AsyncSession):
        self.repo = NotificationRepository(session)

    async def create(self, *, recipient_id: UUID, type: str, title: str, body: str | None = None,
                      entity_type: str | None = None, entity_id: UUID | None = None,
                      delivery_channel: str = "in_app", sender_id: UUID | None = None) -> dict:
        return await self.repo.create({
            "recipient_id": str(recipient_id), "type": type, "title": title, "body": body,
            "entity_type": entity_type, "entity_id": str(entity_id) if entity_id else None,
            "delivery_channel": delivery_channel, "sender_id": str(sender_id) if sender_id else None,
        })

    async def list_for_user(self, recipient_id: UUID, *, unread_only: bool = False) -> list[dict]:
        return await self.repo.list_for_recipient(recipient_id, unread_only=unread_only)

    async def unread_count(self, recipient_id: UUID) -> int:
        return await self.repo.unread_count(recipient_id)

    async def mark_read(self, recipient_id: UUID, notification_ids: list[UUID] | None) -> int:
        return await self.repo.mark_read(recipient_id, notification_ids)
