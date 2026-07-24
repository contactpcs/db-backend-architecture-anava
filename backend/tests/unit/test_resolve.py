"""Regression test for app/core/resolve.py — the shared id-resolution
helper extracted from clinical/scheduling/store/anamnesis/prs/files
(previously copy-pasted near-verbatim in each, found during the eng
review). Deferred imports inside each resolve_* function mean the patch
target is the repository's defining module, not app.core.resolve."""

from unittest.mock import AsyncMock, patch

import pytest

from app.core.exceptions import NotFoundError
from app.core.resolve import (
    resolve_ca_profile_id,
    resolve_doctor_profile_id,
    resolve_patient_profile_id,
)


@pytest.mark.asyncio
async def test_resolve_patient_profile_id_returns_profile_id():
    with patch("app.modules.patients.repository.PatientRepository") as MockRepo:
        MockRepo.return_value.get = AsyncMock(return_value={"profile_id": "profile-1"})
        result = await resolve_patient_profile_id(session=object(), patient_id="patient-1")
    assert result == "profile-1"


@pytest.mark.asyncio
async def test_resolve_patient_profile_id_raises_when_missing():
    with patch("app.modules.patients.repository.PatientRepository") as MockRepo:
        MockRepo.return_value.get = AsyncMock(return_value=None)
        with pytest.raises(NotFoundError) as exc_info:
            await resolve_patient_profile_id(session=object(), patient_id="does-not-exist")
    assert exc_info.value.code == "PATIENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_resolve_doctor_profile_id_returns_profile_id():
    with patch("app.modules.staff.repository.DoctorRepository") as MockRepo:
        MockRepo.return_value.get = AsyncMock(return_value={"profile_id": "profile-2"})
        result = await resolve_doctor_profile_id(session=object(), doctor_id="doctor-1")
    assert result == "profile-2"


@pytest.mark.asyncio
async def test_resolve_doctor_profile_id_raises_when_missing():
    with patch("app.modules.staff.repository.DoctorRepository") as MockRepo:
        MockRepo.return_value.get = AsyncMock(return_value=None)
        with pytest.raises(NotFoundError) as exc_info:
            await resolve_doctor_profile_id(session=object(), doctor_id="does-not-exist")
    assert exc_info.value.code == "DOCTOR_NOT_FOUND"


@pytest.mark.asyncio
async def test_resolve_ca_profile_id_returns_profile_id():
    with patch("app.modules.staff.repository.ClinicalAssistantRepository") as MockRepo:
        MockRepo.return_value.get = AsyncMock(return_value={"profile_id": "profile-3"})
        result = await resolve_ca_profile_id(session=object(), ca_id="ca-1")
    assert result == "profile-3"


@pytest.mark.asyncio
async def test_resolve_ca_profile_id_raises_when_missing():
    with patch("app.modules.staff.repository.ClinicalAssistantRepository") as MockRepo:
        MockRepo.return_value.get = AsyncMock(return_value=None)
        with pytest.raises(NotFoundError) as exc_info:
            await resolve_ca_profile_id(session=object(), ca_id="does-not-exist")
    assert exc_info.value.code == "CA_NOT_FOUND"
