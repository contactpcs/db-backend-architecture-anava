from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ClinicalActivityEntry(BaseModel):
    """One "who did what, when" row of a patient's clinical timeline."""

    occurred_at: datetime
    # protocol_created | protocol_amended | protocol_updated |
    # protocol_activated | protocol_cancelled | protocol_completed |
    # prs_taken | anamnesis_taken | device_session_run |
    # consultation_checked_in | consultation_started | consultation_completed
    action: str
    actor_id: UUID | None
    actor_name: str | None
    actor_role: str | None
    entity_type: str
    entity_id: str
    details: dict[str, Any]
