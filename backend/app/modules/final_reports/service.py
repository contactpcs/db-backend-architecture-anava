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

    async def _assemble(self, patient: dict, scope: str, ctx: RequestContext, selection: dict | None = None) -> dict:
        """Builds the report sections plus everything the doctor can pick from.

        `selection` holds, per section, the ids the doctor ticked (see
        schemas.FinalReportSelection). A missing or None key falls back to the
        scope's default; a given list replaces it — also the scope's window and
        the PRS row caps. Every list is matched against the patient's own
        records, so an id from someone else's record finds nothing (and a
        foreign clinical note is refused outright).

        Returns {"sections", "selection" (the ids the sections actually hold),
        "candidates", "window"}."""
        profile_id = patient["profile_id"]
        now = datetime.now(UTC)
        picked = {k: (None if v is None else {str(x) for x in v}) for k, v in (selection or {}).items()}

        window = {"consultation": None, "start": None, "end": None}
        if scope == "last_consultation":
            window = assembler.consultation_window(await self.repo.started_consultations(profile_id), now)
            if not window["consultation"]:
                raise ConflictError("This patient has no started consultation to report on", code="NO_CONSULTATION")
        consultation = window["consultation"]

        # 01 Anamnesis
        all_anamnesis = await self.repo.anamnesis_assessments(profile_id)
        if picked.get("anamnesis_ids") is not None:
            chosen_anamnesis = [a for a in all_anamnesis if a["anamnesis_id"] in picked["anamnesis_ids"]]
        elif consultation:
            chosen_anamnesis = [a for a in all_anamnesis if str(a.get("appointment_id")) == str(consultation["appointment_id"])]
        else:
            chosen_anamnesis = all_anamnesis
        # Built for every record, not only the chosen ones: the pencil shows
        # each earlier anamnesis in full.
        option_labels = await self.repo.anamnesis_option_labels()
        built_anamnesis = {}
        for a in all_anamnesis:
            questions = await self.repo.anamnesis_questions_with_answers(a["anamnesis_id"], a["assessment_stage"])
            built_anamnesis[a["anamnesis_id"]] = assembler.build_anamnesis(a, questions, option_labels)
        anamnesis = [built_anamnesis[a["anamnesis_id"]] for a in chosen_anamnesis]

        # 07 Treatment protocol (the PRS blocks and the ICD-10 codes follow it)
        protocols = ProtocolService(self.session)
        listed = await protocols.list(ctx, patient_id=patient["patient_id"], limit=200)
        listed.sort(key=lambda p: p["created_at"], reverse=True)
        protocol_candidates = assembler.protocol_candidates(listed)
        if picked.get("protocol_ids") is not None:
            chosen_protocols = [
                p for p in listed if str(p["protocol_id"]) in picked["protocol_ids"] and p["status"] not in ("draft", "cancelled")
            ]
        else:
            chosen_protocols = assembler.pick_protocols(listed, scope)
        details = [await protocols.get_detail(p["protocol_id"], ctx) for p in chosen_protocols]

        # 04 PRS
        instances = await self.repo.prs_instances(profile_id)
        weights: dict[str, dict[str, float]] = {}
        # disease_id is NULL on a general registration PRS: no formula to look up.
        for disease_id in {i["disease_id"] for i in instances if i["disease_id"]}:
            formula = await self.scale_results.active_disease_formula(disease_id)
            weights[disease_id] = formula["config"]["weights"] if formula else {}
        prs_inputs = (
            instances,
            await self.scale_results.list_for_instances([i["instance_id"] for i in instances]),
            weights,
            await self.repo.conditions_by_protocol(sorted({str(i["protocol_id"]) for i in instances if i.get("protocol_id")})),
            # Every device the patient is on gets a PRS block, PRS or not.
            assembler.protocol_instances(details),
        )
        prs = assembler.build_prs(
            *prs_inputs,
            window_start=window["start"],
            window_end=window["end"],
            now=now,
            selected_main=picked.get("prs_ids"),
            selected_sessions=picked.get("session_prs_ids"),
        )
        # The pencil offers every PRS record, each with what its row would print.
        prs_candidates = assembler.prs_candidates(instances, prs_inputs[1], weights, prs_inputs[3])

        # 05 Doctor's notes
        notes = await self.notes.list_for_patient(profile_id)
        if picked.get("note_ids") is not None:
            note_ids = sorted(picked["note_ids"])
        else:
            note_ids = [
                str(n["note_id"])
                for n in notes
                if scope == "overall"
                or str(n.get("appointment_id")) == str(consultation["appointment_id"])
                or assembler.in_window(n["created_at"], window["start"], window["end"])
            ]
        try:
            doctor_notes = assembler.select_notes(notes, note_ids)
        except KeyError as exc:
            raise PermissionError_("A selected note does not belong to this patient", code="NOTE_NOT_OWNED") from exc

        # 06 Medication
        medicines = await self.medicines.list_for_patient(profile_id)
        chosen_medicines = (
            medicines if picked.get("medicine_ids") is None else [m for m in medicines if str(m["medicine_id"]) in picked["medicine_ids"]]
        )

        # 08 Device session details
        session_rows = await self.repo.device_sessions(profile_id)

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
            "doctor_notes": doctor_notes,
            "medications": assembler.build_medications(chosen_medicines),
            # Instance 1, 2, ... — the same order as the device-session PRS blocks.
            "treatment_plans": [
                assembler.build_treatment_plan(d)
                for d in sorted(details, key=lambda d: (d.get("instance_number") is None, d.get("instance_number") or 0))
            ],
            "sessions": assembler.build_sessions(
                session_rows, window_start=window["start"], window_end=window["end"], selected=picked.get("session_ids")
            ),
        }
        effective = {
            "anamnesis_ids": [a["anamnesis_id"] for a in anamnesis],
            "prs_ids": [e["instance_id"] for d in prs["diseases"] for e in d["entries"]],
            "session_prs_ids": [e["instance_id"] for g in prs["device_sessions"] for e in g["entries"]],
            "note_ids": [str(n["note_id"]) for n in doctor_notes],
            "medicine_ids": [str(m["medicine_id"]) for m in sections["medications"]],
            "protocol_ids": [str(t["protocol_id"]) for t in sections["treatment_plans"]],
            "session_ids": [str(x["appointment_id"]) for x in sections["sessions"]],
        }
        candidates = {
            "anamnesis": [
                {
                    "id": a["anamnesis_id"],
                    "recorded_at": a.get("completed_at") or a.get("created_at"),
                    "status": a["status"],
                    "sections": built_anamnesis[a["anamnesis_id"]]["sections"],
                }
                for a in all_anamnesis
            ],
            "prs": prs_candidates["main"],
            "session_prs": prs_candidates["sessions"],
            "notes": [
                {
                    "id": n["note_id"],
                    "category": n["category"],
                    "note_text": n["note_text"],
                    "created_at": n["created_at"],
                    "doctor_name": n.get("doctor_name"),
                }
                for n in notes
            ],
            "medicines": [
                {
                    "id": m["medicine_id"],
                    "medicine_name": m["medicine_name"],
                    "dose": m.get("dose"),
                    "status": m["status"],
                    "started_at": m.get("started_at"),
                }
                for m in medicines
            ],
            "protocols": protocol_candidates,
            "sessions": [
                {
                    "id": r["appointment_id"],
                    "session_number": r.get("session_number"),
                    "date": r["appointment_date"],
                    "device_name": r.get("device_name"),
                    "status": r["session_status"],
                }
                for r in session_rows
            ],
            # 02's pencil: what the doctor wrote on this patient's earlier reports.
            "diagnoses": [
                {"report_id": r["report_id"], "diagnosis": r["diagnosis"], "signed_at": r["signed_at"]}
                for r in await self.repo.list_for_patient(profile_id)
            ],
        }
        return {"sections": sections, "selection": effective, "candidates": candidates, "window": window}

    async def preview(self, patient_id: UUID, scope: str, ctx: RequestContext, selection: dict | None = None) -> dict:
        patient = await self._patient_for_doctor(patient_id, ctx)
        built = await self._assemble(patient, scope, ctx, selection)
        return jsonable_encoder(
            {"scope": scope, "sections": built["sections"], "selection": built["selection"], "candidates": built["candidates"]}
        )

    # -- sign ------------------------------------------------------------------

    async def sign(self, patient_id: UUID, body, ctx: RequestContext) -> dict:
        if not body.confirm_sign:
            raise ValidationError("Confirm the signature to sign the report", code="SIGNATURE_NOT_CONFIRMED")
        diagnosis = body.diagnosis.strip()
        if not diagnosis:
            raise ValidationError("Diagnosis is required to sign the report", code="DIAGNOSIS_REQUIRED")

        patient = await self._patient_for_doctor(patient_id, ctx)
        selection = body.selection.model_dump() if body.selection else {}
        # note_ids at the top level predates `selection`; still honoured.
        if selection.get("note_ids") is None:
            selection["note_ids"] = [str(n) for n in body.note_ids]
        built = await self._assemble(patient, body.scope, ctx, selection)
        sections, window = built["sections"], built["window"]

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
