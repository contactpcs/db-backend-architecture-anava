"""Final Report — Documents/Anava_Final_Report_Plan_v1.md.

preview() assembles the sections live and stores nothing. sign() re-assembles
them server-side (the client never supplies the snapshot), freezes the result
into core.final_reports and emits final_report_signed. A signed report is
immutable (SQL/v1/117): a correction is a new report, older ones stay."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import RequestContext
from app.core.events import emit_event
from app.core.exceptions import ConflictError, NotFoundError, PermissionError_, ValidationError
from app.core.scoping import assert_clinic_scope, assert_owns_profile
from app.modules.final_reports import assembler
from app.modules.final_reports.repository import FinalReportRepository
from app.modules.patients.repository import PatientClinicalNoteRepository, PrescribedMedicineRepository
from app.modules.patients.service import PatientService
from app.modules.prs.repository import PrsScaleResultRepository
from app.modules.treatment_protocols.service import ProtocolService

SIGNATURE_STYLE = "handwritten"


class FinalReportService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = FinalReportRepository(session)
        self.notes = PatientClinicalNoteRepository(session)
        self.medicines = PrescribedMedicineRepository(session)
        self.scale_results = PrsScaleResultRepository(session)

    # -- access ----------------------------------------------------------------

    async def _patient_for_doctor(self, patient_id: UUID, ctx: RequestContext) -> dict:
        """The doctor generates only for a patient of their own clinic whom
        they actually treat. RLS scopes the reads as well; this fails loudly
        with a 403 instead of an empty report."""
        patient = await PatientService(self.session).get(patient_id)
        if str(patient["primary_clinic_id"]) != str(ctx.clinic_id):
            raise PermissionError_("You can only report on patients of your own clinic", code="CLINIC_SCOPE_MISMATCH")
        if not await self.repo.doctor_treats_patient(UUID(ctx.user_id), patient["profile_id"]):
            raise PermissionError_("You can only report on your own patients", code="PATIENT_NOT_ASSIGNED")
        return patient

    # -- assembly --------------------------------------------------------------

    async def _assemble(self, patient: dict, scope: str, ctx: RequestContext) -> tuple[dict, list[dict], dict]:
        """Returns (sections, every clinical note of the patient, window).
        sections["doctor_notes"] is left empty — sign() fills it from the
        doctor's selection."""
        profile_id = patient["profile_id"]
        now = datetime.now(UTC)

        window = {"consultation": None, "start": None, "end": None}
        if scope == "last_consultation":
            window = assembler.consultation_window(await self.repo.started_consultations(profile_id), now)
            if not window["consultation"]:
                raise ConflictError("This patient has no started consultation to report on", code="NO_CONSULTATION")
        consultation = window["consultation"]

        option_labels = await self.repo.anamnesis_option_labels()
        anamnesis = []
        for a in await self.repo.anamnesis_assessments(profile_id, appointment_id=consultation["appointment_id"] if consultation else None):
            questions = await self.repo.anamnesis_questions_with_answers(a["anamnesis_id"], a["assessment_stage"])
            anamnesis.append(assembler.build_anamnesis(a, questions, option_labels))

        protocols = ProtocolService(self.session)
        listed = await protocols.list(ctx, patient_id=patient["patient_id"], limit=200)
        listed.sort(key=lambda p: p["created_at"], reverse=True)
        details = [await protocols.get_detail(p["protocol_id"], ctx) for p in assembler.pick_protocols(listed, scope)]

        instances = await self.repo.prs_instances(profile_id)
        weights: dict[str, dict[str, float]] = {}
        # disease_id is NULL on a general registration PRS: no formula to look up.
        for disease_id in {i["disease_id"] for i in instances if i["disease_id"]}:
            formula = await self.scale_results.active_disease_formula(disease_id)
            weights[disease_id] = formula["config"]["weights"] if formula else {}
        prs = assembler.build_prs(
            instances,
            await self.scale_results.list_for_instances([i["instance_id"] for i in instances]),
            weights,
            await self.repo.conditions_by_protocol(sorted({str(i["protocol_id"]) for i in instances if i.get("protocol_id")})),
            # Every device the patient is on gets a PRS block, PRS or not.
            assembler.protocol_instances(details),
            window_start=window["start"],
            window_end=window["end"],
            now=now,
        )

        sections = {
            "scope": scope,
            "generated_at": now,
            "window": {"from": window["start"], "to": window["end"], "consultation": consultation},
            "patient": await self.repo.patient_header(profile_id),
            "doctor": await self.repo.doctor_header(UUID(ctx.user_id)),
            "clinic": await self.repo.clinic_header(patient["primary_clinic_id"]),
            "anamnesis": anamnesis,
            "diagnosis_codes": assembler.diagnosis_codes(details),
            # Not built yet (plan decision 6) — the slot is reserved so a
            # signed report's shape does not change when it lands.
            "brain_mapping": {"available": False},
            "prs": prs,
            "doctor_notes": [],
            "medications": assembler.build_medications(await self.medicines.list_for_patient(profile_id)),
            # Instance 1, 2, ... — the same order as the device-session PRS blocks.
            "treatment_plans": [
                assembler.build_treatment_plan(d)
                for d in sorted(details, key=lambda d: (d.get("instance_number") is None, d.get("instance_number") or 0))
            ],
            "sessions": assembler.build_sessions(
                await self.repo.device_sessions(profile_id), window_start=window["start"], window_end=window["end"]
            ),
        }
        return sections, await self.notes.list_for_patient(profile_id), window

    async def preview(self, patient_id: UUID, scope: str, ctx: RequestContext) -> dict:
        patient = await self._patient_for_doctor(patient_id, ctx)
        sections, notes, window = await self._assemble(patient, scope, ctx)
        consultation = window["consultation"]
        candidates = [
            {
                **n,
                "in_scope": scope == "overall"
                or (consultation is not None and str(n.get("appointment_id")) == str(consultation["appointment_id"]))
                or (scope == "last_consultation" and assembler.in_window(n["created_at"], window["start"], window["end"])),
            }
            for n in notes
        ]
        return {"scope": scope, "sections": jsonable_encoder(sections), "note_candidates": candidates}

    # -- sign ------------------------------------------------------------------

    async def sign(self, patient_id: UUID, body, ctx: RequestContext) -> dict:
        if not body.confirm_sign:
            raise ValidationError("Confirm the signature to sign the report", code="SIGNATURE_NOT_CONFIRMED")
        diagnosis = body.diagnosis.strip()
        if not diagnosis:
            raise ValidationError("Diagnosis is required to sign the report", code="DIAGNOSIS_REQUIRED")

        patient = await self._patient_for_doctor(patient_id, ctx)
        sections, notes, window = await self._assemble(patient, body.scope, ctx)
        try:
            sections["doctor_notes"] = assembler.select_notes(notes, [str(n) for n in body.note_ids])
        except KeyError as exc:
            raise PermissionError_("A selected note does not belong to this patient", code="NOTE_NOT_OWNED") from exc

        doctor = sections["doctor"] or {}
        snapshot = jsonable_encoder(sections)
        signature = jsonable_encoder(
            {
                "typed_name": doctor.get("name"),
                "license_number": doctor.get("license_number"),
                "style": SIGNATURE_STYLE,
                "signed_at": sections["generated_at"],
            }
        )
        consultation = window["consultation"]
        row = await self.repo.create(
            {
                "patient_id": str(patient["profile_id"]),
                "clinic_id": str(patient["primary_clinic_id"]),
                "doctor_id": ctx.user_id,
                "scope": body.scope,
                "appointment_id": str(consultation["appointment_id"]) if consultation else None,
                "diagnosis": diagnosis,
                "snapshot": snapshot,
                "signature": signature,
                "content_hash": assembler.content_hash(diagnosis, snapshot, signature),
                "signed_at": sections["generated_at"],
            }
        )
        # The relay turns this into the patient's "report is ready"
        # notification (workers/event_relay.py).
        await emit_event(
            self.session,
            aggregate_type="final_report",
            aggregate_id=row["report_id"],
            event_type="final_report_signed",
            payload={
                "report_id": str(row["report_id"]),
                "patient_id": str(patient["profile_id"]),
                "doctor_id": ctx.user_id,
                "scope": body.scope,
            },
        )
        return {**row, "doctor_name": doctor.get("name")}

    # -- read ------------------------------------------------------------------

    async def get(self, report_id: UUID, ctx: RequestContext) -> dict:
        report = await self.repo.get(report_id)
        if not report:
            # Also what RLS leaves when the row is another clinic's.
            raise NotFoundError("Final report not found", code="FINAL_REPORT_NOT_FOUND")
        assert_owns_profile(ctx, report["patient_id"])
        await assert_clinic_scope(ctx, self.session, report["clinic_id"])
        return report

    async def list_for_patient(self, patient_id: UUID, ctx: RequestContext) -> list[dict]:
        patient = await PatientService(self.session).get(patient_id)
        await assert_clinic_scope(ctx, self.session, patient["primary_clinic_id"])
        return await self.repo.list_for_patient(patient["profile_id"])

    async def list_mine(self, ctx: RequestContext, *, scope: str | None, date_from, date_to, newest_first: bool) -> list[dict]:
        return await self.repo.list_for_patient(
            UUID(ctx.user_id), scope=scope, date_from=date_from, date_to=date_to, newest_first=newest_first
        )
