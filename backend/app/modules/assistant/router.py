from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.db import RequestContext, get_db
from app.core.permissions import require_role
from app.modules.assistant import schemas as s
from app.modules.assistant.service import AssistantService

# Patient app only (design brief scope): no staff routes.
router = APIRouter()
_patient = require_role("patient")


@router.get("/me/assistant", response_model=s.AssistantStatus)
async def assistant_status(db=Depends(get_db), ctx: RequestContext = Depends(_patient)):
    """Before opening the chat: consent state, Demo label, today's usage."""
    return await AssistantService(db).status(ctx)


@router.get("/me/assistant/consent/{kind}", response_model=s.ConsentState)
async def get_consent(kind: Literal["assistant", "feedback"], db=Depends(get_db), ctx: RequestContext = Depends(_patient)):
    return await AssistantService(db).consent_state(ctx, kind)


@router.post("/me/assistant/consent", response_model=s.ConsentState)
async def accept_consent(body: s.ConsentAccept, db=Depends(get_db), ctx: RequestContext = Depends(_patient)):
    return await AssistantService(db).accept_consent(ctx, body.kind, body.language)


@router.post("/me/assistant/consent/{kind}/withdraw", response_model=s.ConsentState)
async def withdraw_consent(kind: Literal["assistant", "feedback"], db=Depends(get_db), ctx: RequestContext = Depends(_patient)):
    return await AssistantService(db).withdraw_consent(ctx, kind)


@router.post("/me/assistant/messages", response_model=s.AskReply)
async def ask(body: s.AskRequest, db=Depends(get_db), ctx: RequestContext = Depends(_patient)):
    """One question in, one reply out (safety screen, own-data answer, stored
    answer, AI answer or fallback). Both are saved to the patient's history."""
    return await AssistantService(db).ask(ctx, body)


@router.get("/me/assistant/messages", response_model=list[s.MessageRead])
async def history(
    conversation_id: UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db=Depends(get_db),
    ctx: RequestContext = Depends(_patient),
):
    return await AssistantService(db).history(ctx, conversation_id, limit)


@router.get("/me/feedback/pending", response_model=list[s.PendingFeedback])
async def pending_feedback(db=Depends(get_db), ctx: RequestContext = Depends(_patient)):
    """Completed visits from the last 48 hours still waiting for feedback."""
    return await AssistantService(db).pending_feedback(ctx)


@router.post("/me/appointments/{appointment_id}/feedback", response_model=s.FeedbackResult, status_code=201)
async def submit_feedback(appointment_id: UUID, body: s.FeedbackSubmit, db=Depends(get_db), ctx: RequestContext = Depends(_patient)):
    return await AssistantService(db).submit_feedback(ctx, appointment_id, body)
