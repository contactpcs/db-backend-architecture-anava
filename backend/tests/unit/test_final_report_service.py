"""Final Report gates — who may generate, what must be true to sign, who may
read. Collaborators are mocked, so no database is needed. The database-side
lock and RLS are covered by tests/integration/test_final_reports.py."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.db import RequestContext
from app.core.exceptions import ConflictError, NotFoundError, PermissionError_, ValidationError
from app.modules.final_reports import assembler, router
from app.modules.final_reports import service as service_module
from app.modules.final_reports.schemas import FinalReportSign
from app.modules.final_reports.service import FinalReportService

DOCTOR = "00000000-0000-0000-0000-0000000000d1"
CLINIC = "00000000-0000-0000-0000-0000000000c1"
PATIENT_PROFILE = "00000000-0000-0000-0000-0000000000a1"
NOTE = "00000000-0000-0000-0000-0000000000e1"
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def _doctor_ctx(clinic_id: str = CLINIC) -> RequestContext:
    return RequestContext(user_id=DOCTOR, role="doctor", clinic_id=clinic_id, region_id=None)


def _patient(clinic_id: str = CLINIC) -> dict:
    return {"patient_id": uuid4(), "profile_id": PATIENT_PROFILE, "primary_clinic_id": clinic_id}


def _sections() -> dict:
    return {"scope": "overall", "generated_at": NOW, "doctor": {"name": "Asha Rao", "license_number": "KA-123"}, "doctor_notes": []}


def _notes() -> list[dict]:
    return [{"note_id": NOTE, "category": "General", "note_text": "Improving", "created_at": NOW, "appointment_id": None}]


def _body(**overrides) -> FinalReportSign:
    return FinalReportSign(**{"scope": "overall", "diagnosis": "MDD + mild psychotic episodes", "confirm_sign": True, **overrides})


def _built(window: dict | None = None, notes: list[dict] | None = None) -> dict:
    sections = _sections()
    sections["doctor_notes"] = notes or []
    return {
        "sections": sections,
        "selection": {"note_ids": [n["note_id"] for n in notes or []]},
        "candidates": {},
        "window": window or {"consultation": None, "start": None, "end": None},
    }


@pytest.fixture
def service():
    """A service whose patient lookup and section assembly are stubbed: the
    patient belongs to the doctor's clinic and is treated by the doctor."""
    svc = FinalReportService(AsyncMock())
    svc.repo = AsyncMock()
    svc.repo.doctor_treats_patient.return_value = True
    svc.repo.create.side_effect = lambda data: {**data, "report_id": uuid4()}
    svc._assemble = AsyncMock(return_value=_built(notes=_notes()))
    with (
        patch.object(service_module, "PatientService") as patients,
        patch.object(service_module, "emit_event", new=AsyncMock()) as emit,
    ):
        patients.return_value.get = AsyncMock(return_value=_patient())
        svc.patients, svc.emit = patients, emit
        yield svc


# -- sign gates ----------------------------------------------------------------


async def test_unconfirmed_signature_is_refused_before_anything_is_read(service):
    with pytest.raises(ValidationError) as exc:
        await service.sign(uuid4(), _body(confirm_sign=False), _doctor_ctx())
    assert exc.value.code == "SIGNATURE_NOT_CONFIRMED"
    service.repo.create.assert_not_called()


@pytest.mark.parametrize("diagnosis", ["", "   ", "\n\t"])
async def test_blank_diagnosis_is_refused(service, diagnosis):
    with pytest.raises(ValidationError) as exc:
        await service.sign(uuid4(), _body(diagnosis=diagnosis), _doctor_ctx())
    assert exc.value.code == "DIAGNOSIS_REQUIRED"
    service.repo.create.assert_not_called()


async def test_patient_of_another_clinic_is_refused(service):
    service.patients.return_value.get = AsyncMock(return_value=_patient(clinic_id=str(uuid4())))
    with pytest.raises(PermissionError_) as exc:
        await service.sign(uuid4(), _body(), _doctor_ctx())
    assert exc.value.code == "CLINIC_SCOPE_MISMATCH"
    service.repo.create.assert_not_called()


async def test_patient_the_doctor_does_not_treat_is_refused(service):
    service.repo.doctor_treats_patient.return_value = False
    with pytest.raises(PermissionError_) as exc:
        await service.sign(uuid4(), _body(), _doctor_ctx())
    assert exc.value.code == "PATIENT_NOT_ASSIGNED"
    with pytest.raises(PermissionError_):
        await service.preview(uuid4(), "overall", _doctor_ctx())


async def test_last_consultation_without_a_started_consultation_is_a_conflict():
    svc = FinalReportService(AsyncMock())
    svc.repo = AsyncMock()
    svc.repo.started_consultations.return_value = []
    with pytest.raises(ConflictError) as exc:
        await svc._assemble(_patient(), "last_consultation", _doctor_ctx())
    assert exc.value.code == "NO_CONSULTATION"


# -- what gets stored ----------------------------------------------------------


async def test_signing_freezes_the_sections_with_the_doctors_name_and_a_matching_hash(service):
    report = await service.sign(uuid4(), _body(diagnosis="  MDD  ", note_ids=[NOTE]), _doctor_ctx())

    stored = service.repo.create.call_args.args[0]
    assert stored["diagnosis"] == "MDD"
    assert (stored["patient_id"], stored["clinic_id"], stored["doctor_id"]) == (PATIENT_PROFILE, CLINIC, DOCTOR)
    assert stored["appointment_id"] is None
    # The signature is the doctor's own profile, never something the client sent.
    assert stored["signature"] == {
        "typed_name": "Asha Rao",
        "license_number": "KA-123",
        "style": "handwritten",
        "signed_at": NOW.isoformat(),
    }
    assert [n["note_text"] for n in stored["snapshot"]["doctor_notes"]] == ["Improving"]
    assert stored["content_hash"] == assembler.content_hash("MDD", stored["snapshot"], stored["signature"])
    assert report["doctor_name"] == "Asha Rao"


async def test_signing_rebuilds_the_report_from_the_doctors_selection(service):
    picked = {"anamnesis_ids": ["ANA-2"], "prs_ids": ["i9"], "medicine_ids": [str(uuid4())]}
    await service.sign(uuid4(), _body(selection=picked), _doctor_ctx())
    selection = service._assemble.call_args.args[3]
    assert selection["anamnesis_ids"] == ["ANA-2"] and selection["prs_ids"] == ["i9"]
    assert selection["protocol_ids"] is None  # not picked: the scope's default
    # The older top-level note_ids still count when the selection leaves notes out.
    assert selection["note_ids"] == []
    await service.sign(uuid4(), _body(note_ids=[NOTE]), _doctor_ctx())
    assert service._assemble.call_args.args[3]["note_ids"] == [NOTE]


async def test_signing_emits_the_event_that_notifies_the_patient(service):
    report = await service.sign(uuid4(), _body(), _doctor_ctx())
    event = service.emit.call_args.kwargs
    assert (event["aggregate_type"], event["event_type"]) == ("final_report", "final_report_signed")
    assert event["payload"]["patient_id"] == PATIENT_PROFILE
    assert event["payload"]["report_id"] == str(report["report_id"])


async def test_last_consultation_report_records_its_appointment(service):
    appointment_id = uuid4()
    service._assemble = AsyncMock(
        return_value=_built(window={"consultation": {"appointment_id": appointment_id}, "start": None, "end": NOW})
    )
    await service.sign(uuid4(), _body(scope="last_consultation"), _doctor_ctx())
    assert service.repo.create.call_args.args[0]["appointment_id"] == str(appointment_id)


async def test_preview_stores_nothing(service):
    preview = await service.preview(uuid4(), "overall", _doctor_ctx())
    assert set(preview) == {"scope", "sections", "selection", "candidates"}
    service.repo.create.assert_not_called()
    service.emit.assert_not_called()


# -- the real assembly: defaults per scope, and the doctor's picks -------------

ANA_1, ANA_2 = "ANA-1", "ANA-2"
VISIT = uuid4()
MED_A, MED_B = uuid4(), uuid4()
FOREIGN_NOTE = "00000000-0000-0000-0000-0000000000f9"


@pytest.fixture
def assembling():
    """FinalReportService with every data source stubbed: two consultation
    anamneses (ANA-2 belongs to VISIT), two notes (one written at VISIT), two
    medicines, one active protocol, no PRS, no device sessions."""
    svc = FinalReportService(AsyncMock())
    svc.repo = AsyncMock()
    svc.repo.started_consultations.return_value = [
        {"appointment_id": uuid4(), "started_at": datetime(2026, 9, 1, tzinfo=UTC), "completed_at": datetime(2026, 9, 1, 1, tzinfo=UTC)},
        {"appointment_id": VISIT, "started_at": datetime(2026, 10, 1, tzinfo=UTC), "completed_at": datetime(2026, 10, 1, 1, tzinfo=UTC)},
    ]
    svc.repo.anamnesis_assessments.return_value = [
        {"anamnesis_id": ANA_2, "assessment_stage": "main", "status": "completed", "appointment_id": VISIT, "created_at": NOW},
        {"anamnesis_id": ANA_1, "assessment_stage": "main", "status": "completed", "appointment_id": uuid4(), "created_at": NOW},
    ]
    svc.repo.anamnesis_questions_with_answers.return_value = []
    svc.repo.anamnesis_option_labels.return_value = {}
    svc.repo.prs_instances.return_value = []
    svc.repo.conditions_by_protocol.return_value = {}
    svc.repo.device_sessions.return_value = []
    svc.repo.list_for_patient.return_value = [{"report_id": uuid4(), "diagnosis": "GAD", "signed_at": NOW}]
    svc.scale_results = AsyncMock()
    svc.scale_results.list_for_instances.return_value = []
    svc.notes = AsyncMock()
    svc.notes.list_for_patient.return_value = [
        {
            "note_id": NOTE,
            "category": "General",
            "note_text": "At the visit",
            "created_at": datetime(2026, 10, 1, 0, 30, tzinfo=UTC),
            "appointment_id": VISIT,
        },
        {
            "note_id": str(uuid4()),
            "category": "General",
            "note_text": "Months ago",
            "created_at": datetime(2026, 5, 1, tzinfo=UTC),
            "appointment_id": None,
        },
    ]
    svc.medicines = AsyncMock()
    svc.medicines.list_for_patient.return_value = [
        {"medicine_id": MED_A, "medicine_name": "A", "status": "active"},
        {"medicine_id": MED_B, "medicine_name": "B", "status": "stopped"},
    ]
    protocol = {
        "protocol_id": uuid4(),
        "status": "active",
        "created_at": NOW,
        "device_name": "Sooma",
        "version_major": 1,
        "version_minor": 0,
    }
    with patch.object(service_module, "ProtocolService") as protocols:
        protocols.return_value.list = AsyncMock(return_value=[protocol])
        protocols.return_value.get_detail = AsyncMock(side_effect=lambda pid, ctx: {**protocol, "protocol_id": pid, "sessions": []})
        yield svc


async def test_overall_by_default_takes_everything(assembling):
    built = await assembling._assemble(_patient(), "overall", _doctor_ctx())
    assert built["selection"]["anamnesis_ids"] == [ANA_2, ANA_1]
    assert len(built["selection"]["note_ids"]) == 2
    assert built["selection"]["medicine_ids"] == [str(MED_A), str(MED_B)]
    assert len(built["selection"]["protocol_ids"]) == 1
    # Every section's pencil gets the full history, ticked or not.
    assert [c["id"] for c in built["candidates"]["anamnesis"]] == [ANA_2, ANA_1]
    assert len(built["candidates"]["notes"]) == 2 and len(built["candidates"]["medicines"]) == 2
    assert built["candidates"]["diagnoses"][0]["diagnosis"] == "GAD"
    # The anamnesis pencil shows each record in full, also the ones not ticked.
    assert all("sections" in c for c in built["candidates"]["anamnesis"])


async def test_the_prs_pencil_shows_every_sitting_with_its_scores(assembling):
    # Two main PRS sittings; only the newer one is in the last-consultation window.
    assembling.repo.prs_instances.return_value = [
        {
            "instance_id": "i2",
            "disease_id": "DEP",
            "disease_name": "Depression",
            "completed_at": datetime(2026, 10, 1, 0, 30, tzinfo=UTC),
            "appointment_type": "follow_up",
        },
        {
            "instance_id": "i1",
            "disease_id": "DEP",
            "disease_name": "Depression",
            "completed_at": datetime(2026, 5, 1, tzinfo=UTC),
            "appointment_type": "initial",
        },
    ]
    assembling.scale_results.list_for_instances.return_value = [
        {
            "instance_id": "i1",
            "scale_code": "PHQ9",
            "scale_name": "PHQ-9",
            "calculated_value": 20,
            "max_possible": 27,
            "direction_corrected_percentage": 74,
        },
        {
            "instance_id": "i2",
            "scale_code": "PHQ9",
            "scale_name": "PHQ-9",
            "calculated_value": 10,
            "max_possible": 27,
            "direction_corrected_percentage": 37,
        },
    ]
    assembling.scale_results.active_disease_formula.return_value = {"config": {"weights": {"PHQ9": 100}}}
    built = await assembling._assemble(_patient(), "last_consultation", _doctor_ctx())
    assert built["selection"]["prs_ids"] == ["i2"]
    offered = {c["id"]: c for c in built["candidates"]["prs"]}
    assert set(offered) == {"i1", "i2"}  # the older sitting is offered too, with its own scores
    assert (offered["i1"]["score"], offered["i1"]["scales"][0]["value"]) == (74.0, 20)
    assert (offered["i2"]["score"], offered["i2"]["scales"][0]["value"]) == (37.0, 10)


async def test_last_consultation_by_default_takes_that_visit_only(assembling):
    built = await assembling._assemble(_patient(), "last_consultation", _doctor_ctx())
    assert built["selection"]["anamnesis_ids"] == [ANA_2]
    assert built["selection"]["note_ids"] == [NOTE]
    assert [c["id"] for c in built["candidates"]["anamnesis"]] == [ANA_2, ANA_1]  # still offered


async def test_the_doctors_picks_replace_the_default(assembling):
    picked = {"anamnesis_ids": [ANA_1], "note_ids": [], "medicine_ids": [MED_B], "protocol_ids": []}
    built = await assembling._assemble(_patient(), "last_consultation", _doctor_ctx(), picked)
    sections = built["sections"]
    assert [a["anamnesis_id"] for a in sections["anamnesis"]] == [ANA_1]  # an older visit, outside the window
    assert sections["doctor_notes"] == []
    assert [m["medicine_name"] for m in sections["medications"]] == ["B"]
    assert sections["treatment_plans"] == []


async def test_ids_from_someone_elses_record_find_nothing(assembling):
    built = await assembling._assemble(
        _patient(), "overall", _doctor_ctx(), {"anamnesis_ids": ["ANA-of-another"], "protocol_ids": [uuid4()]}
    )
    assert built["sections"]["anamnesis"] == [] and built["sections"]["treatment_plans"] == []


async def test_a_note_of_another_patient_is_refused(assembling):
    with pytest.raises(PermissionError_) as exc:
        await assembling._assemble(_patient(), "overall", _doctor_ctx(), {"note_ids": [FOREIGN_NOTE]})
    assert exc.value.code == "NOTE_NOT_OWNED"


# -- read ----------------------------------------------------------------------


async def test_patient_cannot_read_another_patients_report():
    svc = FinalReportService(AsyncMock())
    svc.repo = AsyncMock()
    svc.repo.get.return_value = {"report_id": uuid4(), "patient_id": str(uuid4()), "clinic_id": CLINIC}
    ctx = RequestContext(user_id=PATIENT_PROFILE, role="patient", clinic_id=None, region_id=None)
    with pytest.raises(PermissionError_):
        await svc.get(uuid4(), ctx)


async def test_patient_reads_own_report_and_a_hidden_one_is_404():
    svc = FinalReportService(AsyncMock())
    svc.repo = AsyncMock()
    ctx = RequestContext(user_id=PATIENT_PROFILE, role="patient", clinic_id=None, region_id=None)
    svc.repo.get.return_value = {"report_id": uuid4(), "patient_id": PATIENT_PROFILE, "clinic_id": CLINIC}
    assert (await svc.get(uuid4(), ctx))["patient_id"] == PATIENT_PROFILE
    svc.repo.get.return_value = None  # what RLS leaves for another clinic's row
    with pytest.raises(NotFoundError):
        await svc.get(uuid4(), ctx)


async def test_clinic_admin_cannot_read_another_clinics_report():
    svc = FinalReportService(AsyncMock())
    svc.repo = AsyncMock()
    svc.repo.get.return_value = {"report_id": uuid4(), "patient_id": PATIENT_PROFILE, "clinic_id": str(uuid4())}
    ctx = RequestContext(user_id=str(uuid4()), role="clinic_admin", clinic_id=CLINIC, region_id=None)
    with pytest.raises(PermissionError_):
        await svc.get(uuid4(), ctx)


async def test_my_reports_are_always_the_callers_own():
    svc = FinalReportService(AsyncMock())
    svc.repo = AsyncMock()
    svc.repo.list_for_patient.return_value = []
    ctx = RequestContext(user_id=PATIENT_PROFILE, role="patient", clinic_id=None, region_id=None)
    await svc.list_mine(ctx, scope="overall", date_from=None, date_to=None, newest_first=False)
    assert str(svc.repo.list_for_patient.call_args.args[0]) == PATIENT_PROFILE
    assert svc.repo.list_for_patient.call_args.kwargs["newest_first"] is False


# -- roles ---------------------------------------------------------------------


def _allowed_roles(endpoint) -> set[str]:
    """The roles a route's require_role(...) dependency admits."""
    check = endpoint.__defaults__[-1].dependency
    return set(check.__closure__[0].cell_contents)


def test_only_a_doctor_generates_and_only_doctors_and_admins_read_previous_reports():
    assert _allowed_roles(router.preview_final_report) == {"doctor"}
    assert _allowed_roles(router.sign_final_report) == {"doctor"}
    assert _allowed_roles(router.list_patient_final_reports) == {"doctor", "clinic_admin", "regional_admin", "super_admin"}
    assert _allowed_roles(router.list_my_final_reports) == {"patient"}
    assert _allowed_roles(router.get_final_report) == {"doctor", "clinic_admin", "regional_admin", "super_admin", "patient"}
