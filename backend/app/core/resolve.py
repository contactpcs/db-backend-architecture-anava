"""Shared id-resolution helpers used across modules whose FK columns
reference profiles(id) directly, while the public API accepts the
role-table id instead (patients.patient_id / doctors.doctor_id /
clinical_assistants.ca_id) for consistency with GET /patients/{id} etc.
Two different UUIDs for the same person — these resolve one to the other.
Previously copy-pasted near-verbatim across clinical, scheduling, store,
anamnesis, prs, and files (found during the architecture review)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError


async def resolve_patient_profile_id(session: AsyncSession, patient_id: UUID) -> UUID:
    from app.modules.patients.repository import PatientRepository

    patient = await PatientRepository(session).get(patient_id)
    if not patient:
        raise NotFoundError("Patient not found", code="PATIENT_NOT_FOUND")
    return patient["profile_id"]


async def resolve_doctor_profile_id(session: AsyncSession, doctor_id: UUID) -> UUID:
    from app.modules.staff.repository import DoctorRepository

    doctor = await DoctorRepository(session).get(doctor_id)
    if not doctor:
        raise NotFoundError("Doctor not found", code="DOCTOR_NOT_FOUND")
    return doctor["profile_id"]


async def resolve_ca_profile_id(session: AsyncSession, ca_id: UUID) -> UUID:
    from app.modules.staff.repository import ClinicalAssistantRepository

    ca = await ClinicalAssistantRepository(session).get(ca_id)
    if not ca:
        raise NotFoundError("Clinical assistant not found", code="CA_NOT_FOUND")
    return ca["profile_id"]
