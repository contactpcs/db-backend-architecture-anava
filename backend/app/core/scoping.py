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
    row = (
        await session.execute(text("SELECT region_id FROM clinics WHERE clinic_id = :id"), {"id": str(clinic_id)})
    ).mappings().first()
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
