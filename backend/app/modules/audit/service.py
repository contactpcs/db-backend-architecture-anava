"""Patient clinical activity timeline: who checked in / started / completed
each consultation, who took the PRS, who took the anamnesis, who ran each
device session, who issued / amended / edited / activated / cancelled /
completed each treatment protocol, and when.

Read from the clinical tables' own actor columns (protocol_plan.set_by,
prs_assessment_instances.administered_by, anamnesis_assessments.submitted_by,
device_sessions.performed_by_id) rather than from the log tables, so it
covers every row ever written and cannot drift from the record it describes.
Only the protocol lifecycle steps, which leave no column behind, come from
compliance.activity_logs.

Two views (requirement 2026-10-04):
  full     doctor and admins - everything, including what an edit or an
           amendment changed.
  limited  clinical assistant - who issued each protocol and who took each
           PRS / anamnesis / device session. No lifecycle steps, no diffs.

RLS scopes every table here to the caller's clinic, the app role does not
bypass it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import RequestContext
from app.core.resolve import resolve_patient_profile_id

FULL_TRAIL_ROLES = ("super_admin", "regional_admin", "clinic_admin", "doctor")

# protocol_plan columns that describe the row rather than the prescription;
# a difference in these is not "what the amendment changed".
_NOT_PRESCRIPTION = frozenset(
    {
        "protocol_id",
        "set_by",
        "status",
        "activated_at",
        "completed_at",
        "created_at",
        "updated_at",
        "supersedes_protocol_id",
        "version_major",
        "version_minor",
        "instance_id",
        "authored_in_appointment_id",
    }
)

_PATIENT_PROTOCOLS = "FROM protocol_plan pp JOIN protocol_instances i ON i.instance_id = pp.instance_id WHERE i.patient_id = :pid"

_DOMAIN_ROWS = f"""
    SELECT pp.created_at AS occurred_at,
           CASE WHEN pp.supersedes_protocol_id IS NULL THEN 'protocol_created' ELSE 'protocol_amended' END AS action,
           pp.set_by AS actor_id, NULL::text AS actor_role,
           'treatment_protocol' AS entity_type, pp.protocol_id::text AS entity_id,
           jsonb_build_object('version', pp.version_major || '.' || pp.version_minor, 'status', pp.status,
                              'instance_id', pp.instance_id, 'supersedes_protocol_id', pp.supersedes_protocol_id) AS details
      {_PATIENT_PROTOCOLS}
    UNION ALL
    SELECT COALESCE(a.completed_at, a.started_at), 'prs_taken',
           COALESCE(a.administered_by, a.patient_id), NULL::text,
           'prs_assessment_instance', a.instance_id,
           jsonb_build_object('stage', a.assessment_stage, 'status', a.status, 'is_voided', a.is_voided,
                              'appointment_id', a.appointment_id)
      FROM prs_assessment_instances a WHERE a.patient_id = :pid
    UNION ALL
    SELECT COALESCE(an.completed_at, an.created_at), 'anamnesis_taken',
           COALESCE(an.submitted_by, an.patient_id), NULL::text,
           'anamnesis_assessment', an.anamnesis_id,
           jsonb_build_object('stage', an.assessment_stage, 'status', an.status, 'appointment_id', an.appointment_id)
      FROM anamnesis_assessments an WHERE an.patient_id = :pid
    UNION ALL
    SELECT COALESCE(ds.started_at, ds.created_at), 'device_session_run',
           COALESCE(ds.performed_by_id, ds.created_by), ds.performed_by_role,
           'device_session', ds.device_session_record_id::text,
           jsonb_build_object('session_status', ds.session_status, 'session_number', ap.session_number,
                              'protocol_id', ds.protocol_id, 'completed_at', ds.completed_at)
      FROM device_sessions ds JOIN appointments ap ON ap.appointment_id = ds.appointment_id
     WHERE ap.patient_id = :pid
    UNION ALL
    SELECT l.changed_at,
           CASE l.new_status WHEN 'checked_in' THEN 'consultation_checked_in'
                             WHEN 'in_progress' THEN 'consultation_started'
                             ELSE 'consultation_completed' END,
           l.changed_by, l.changed_by_role,
           'appointment', l.appointment_id::text,
           jsonb_build_object('appointment_type', ap.appointment_type, 'appointment_date', ap.appointment_date)
      FROM appointment_audit_logs l JOIN appointments ap ON ap.appointment_id = l.appointment_id
     WHERE ap.patient_id = :pid AND ap.appointment_type <> 'device_session'
       AND l.new_status IN ('checked_in', 'in_progress', 'completed')
"""

# activity_logs.actor_role is the role held at the time, so it wins over the
# profile's current role in the outer COALESCE.
_LIFECYCLE_ROWS = f"""
    UNION ALL
    SELECT al.created_at, replace(al.event_type, 'treatment_protocol.', 'protocol_'),
           al.actor_id, al.actor_role,
           'treatment_protocol', al.entity_id::text, al.metadata
      FROM activity_logs al
     WHERE al.entity_type = 'treatment_protocol'
       AND al.event_type IN ('treatment_protocol.updated', 'treatment_protocol.activated',
                             'treatment_protocol.cancelled', 'treatment_protocol.completed')
       AND al.entity_id IN (SELECT pp.protocol_id {_PATIENT_PROTOCOLS})
"""


def prescription_changes(old: dict[str, Any], new: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """What an amendment changed against the version it superseded."""
    return {
        key: {"from": old.get(key), "to": value} for key, value in new.items() if key not in _NOT_PRESCRIPTION and old.get(key) != value
    }


class ClinicalActivityService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def for_patient(self, patient_id: UUID, ctx: RequestContext, *, actor_id: UUID | None = None, limit: int = 200) -> list[dict]:
        pid = str(await resolve_patient_profile_id(self.session, patient_id))
        full = ctx.role in FULL_TRAIL_ROLES

        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT t.occurred_at, t.action, t.actor_id, "
                        "p.first_name || ' ' || p.last_name AS actor_name, "
                        "COALESCE(t.actor_role, p.role) AS actor_role, t.entity_type, t.entity_id, t.details "
                        f"FROM ({_DOMAIN_ROWS}{_LIFECYCLE_ROWS if full else ''}) t "
                        "LEFT JOIN profiles p ON p.id = t.actor_id "
                        "WHERE (CAST(:actor AS UUID) IS NULL OR t.actor_id = CAST(:actor AS UUID)) "
                        "ORDER BY t.occurred_at DESC LIMIT :limit"
                    ),
                    {"pid": pid, "actor": str(actor_id) if actor_id else None, "limit": limit},
                )
            )
            .mappings()
            .all()
        )
        entries = [dict(r) for r in rows]
        if full:
            await self._attach_amendment_changes(entries, pid)
        return entries

    async def _attach_amendment_changes(self, entries: list[dict], pid: str) -> None:
        if not any(e["action"] == "protocol_amended" for e in entries):
            return
        # ponytail: diffs protocol_plan columns only. A change limited to the
        # conditions / diagnoses / scales child tables shows as an amendment
        # with no changes, compare those tables too if that proves confusing.
        plans = {
            r["protocol_id"]: r
            for r in (await self.session.execute(text(f"SELECT to_jsonb(pp) AS row {_PATIENT_PROTOCOLS}"), {"pid": pid})).scalars().all()
        }
        for entry in entries:
            if entry["action"] != "protocol_amended":
                continue
            new = plans.get(entry["entity_id"])
            old = plans.get(new["supersedes_protocol_id"]) if new else None
            if new and old:
                entry["details"] = {**entry["details"], "changes": prescription_changes(old, new)}
