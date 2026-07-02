from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class NotificationRead(BaseModel):
    notification_id: UUID
    recipient_id: UUID
    type: str
    delivery_channel: str
    title: str
    body: str | None
    entity_type: str | None
    entity_id: UUID | None
    is_read: bool
    created_at: datetime


class MarkReadRequest(BaseModel):
    notification_ids: list[UUID] | None = None  # None = mark all read
