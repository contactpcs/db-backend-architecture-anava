"""Clinic/region scope checks shared across staff, patients, and admin
modules — regional_admin/clinic_admin are confined to their own
region/clinic (RequestContext.region_id/clinic_id, populated by
AuthContextMiddleware from the admins table). super_admin crosses every
boundary. See Master Doc Section 5.2 / 8 Flow G-I."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import RequestContext
from app.core.exceptions import PermissionError_


async def clinic_region_id(session: AsyncSession, clinic_id) -> str | None:
    row = (await session.execute(text("SELECT region_id FROM clinics WHERE clinic_id = :id"), {"id": str(clinic_id)})).mappings().first()
    return str(row["region_id"]) if row and row["region_id"] else None


async def assert_clinic_scope(ctx: RequestContext, session: AsyncSession, clinic_id) -> None:
    """super_admin passes through untouched. clinic_admin must match
    ctx.clinic_id exactly. regional_admin's clinic must belong to
    ctx.region_id."""
    if ctx.role == "clinic_admin" and str(clinic_id) != ctx.clinic_id:
        raise PermissionError_("You can only manage records for your own clinic", code="CLINIC_SCOPE_MISMATCH")
    if ctx.role == "regional_admin":
        region_id = await clinic_region_id(session, clinic_id)
        if region_id != ctx.region_id:
            raise PermissionError_("You can only manage records for clinics in your own region", code="REGION_SCOPE_MISMATCH")


async def assert_patient_self(ctx: RequestContext, session: AsyncSession, patient_id) -> None:
    """Every anamnesis/PRS/disease-selection endpoint allows role='patient'
    so a patient can work through their own self-registration wizard or
    portal, but the routes take patients.patient_id as a path param with no
    other restriction. RLS is meant to be the real backstop here (see
    SQL/15_rls_policies.sql) but the app's DB role connects as a Postgres
    superuser (rolbypassrls=TRUE), which unconditionally bypasses RLS — so
    without this check, any authenticated patient could read/write any
    OTHER patient's clinical data just by passing their patient_id. No-op
    for staff roles (they're scoped elsewhere, e.g. assert_clinic_scope)."""
    if ctx.role != "patient":
        return
    row = (
        (await session.execute(text("SELECT profile_id FROM patients WHERE patient_id = :id"), {"id": str(patient_id)})).mappings().first()
    )
    if not row or str(row["profile_id"]) != ctx.user_id:
        raise PermissionError_("You can only access your own patient record", code="PATIENT_SCOPE_MISMATCH")


def assert_owns_profile(ctx: RequestContext, profile_id) -> None:
    """Same purpose as assert_patient_self, for records that already store
    the owning profiles.id directly (anamnesis_assessments.patient_id,
    prs_assessment_instances.patient_id — both profiles.id, not
    patients.patient_id; see _resolve_profile_id in anamnesis/service.py
    and prs/service.py). No DB call needed since the caller already has the
    row in hand."""
    if ctx.role == "patient" and str(profile_id) != ctx.user_id:
        raise PermissionError_("You can only access your own patient record", code="PATIENT_SCOPE_MISMATCH")
