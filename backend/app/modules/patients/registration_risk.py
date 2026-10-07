"""Risk checks on a self-registered patient, run at the moment registration
completes (design: Documents/design_signup_auto_approval.md).

A registration with no flag can be approved without a receptionist. Any flag
sends it to the receptionist's queue with the reasons attached. Nothing here
ever rejects anyone.
"""

from __future__ import annotations

import re
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.db import as_system
from app.modules.auth.signup_security import contact_hash

settings = get_settings()

_TEST_WORDS = ("test", "asdf", "qwerty", "abc", "xyz", "dummy", "fake")
_SAME_LETTER_THREE_TIMES = re.compile(r"(.)\1\1")


def name_is_suspicious(first_name: str, last_name: str) -> bool:
    """A single-letter LAST name is fine: an initial as a surname is common in
    South India."""
    first, last = first_name.strip().lower(), last_name.strip().lower()
    full = f"{first} {last}"
    return (
        len(first) <= 1
        or any(ch.isdigit() for ch in full)
        or any(word in full for word in _TEST_WORDS)
        or bool(_SAME_LETTER_THREE_TIMES.search(first) or _SAME_LETTER_THREE_TIMES.search(last))
    )


def dob_is_implausible(dob: date | None, today: date) -> bool:
    return dob is not None and (dob > today or (today - dob).days > settings.signup_max_age_years * 365.25)


async def assess_registration(session: AsyncSession, patient: dict) -> tuple[list[str], str | None]:
    """The flags this registration raises, and the hash of the contact it
    signed up with (for the security log).

    `patient` is the row as PatientRepository.get returns it, read AFTER
    registration was marked complete. Reads as RLS role 'system': a patient
    cannot see the other patients it has to be compared with."""
    async with as_system(session):
        row = (
            (
                await session.execute(
                    text(
                        "SELECT "
                        "EXISTS (SELECT 1 FROM patients o JOIN profiles op ON op.id = o.profile_id "
                        "        WHERE o.patient_id <> :patient_id AND o.primary_clinic_id = :clinic_id AND o.deleted_at IS NULL "
                        "          AND lower(btrim(op.first_name)) = lower(btrim(:first_name)) "
                        "          AND lower(btrim(op.last_name)) = lower(btrim(:last_name)) AND op.dob = :dob) AS duplicate, "
                        "(SELECT count(*) FROM patients o WHERE o.self_registered AND o.signup_ip = CAST(:signup_ip AS INET) "
                        "   AND o.registration_completed_at > now() - interval '24 hours') AS from_same_ip, "
                        "(SELECT CASE WHEN p.email LIKE 'pending-%@no-email.local' THEN p.phone ELSE p.email_normalized END "
                        "   FROM profiles p WHERE p.id = :profile_id) AS contact"
                    ),
                    {
                        "patient_id": str(patient["patient_id"]),
                        "clinic_id": str(patient["primary_clinic_id"]) if patient["primary_clinic_id"] else None,
                        "first_name": patient["first_name"],
                        "last_name": patient["last_name"],
                        "dob": patient["dob"],
                        "signup_ip": str(patient["signup_ip"]) if patient["signup_ip"] else None,
                        "profile_id": str(patient["profile_id"]),
                    },
                )
            )
            .mappings()
            .one()
        )
    wizard_seconds = (patient["registration_completed_at"] - patient["created_at"]).total_seconds()
    checks = {
        "duplicate_patient": row["duplicate"],
        "ip_velocity": row["from_same_ip"] > settings.signup_ip_velocity_limit_per_day,
        "suspicious_name": name_is_suspicious(patient["first_name"], patient["last_name"]),
        "implausible_dob": dob_is_implausible(patient["dob"], date.today()),
        "completed_too_fast": wizard_seconds < settings.signup_min_wizard_seconds,
    }
    return [flag for flag, raised in checks.items() if raised], contact_hash(row["contact"]) if row["contact"] else None


async def record_review(session: AsyncSession, patient: dict, flags: list[str], digest: str | None, *, auto_approved: bool) -> None:
    """One log row per flag, or one for the automatic approval. Written in the
    request's own transaction: if registration does not commit, neither does
    the record of it."""
    outcomes = [("auto_approved", None)] if auto_approved else [("flagged", flag) for flag in flags]
    async with as_system(session):
        for outcome, reason in outcomes:
            await session.execute(
                text(
                    "INSERT INTO signup_security_log (checkpoint, outcome, reason, ip, contact_hash, patient_id) "
                    "VALUES ('registration_complete', :outcome, :reason, CAST(:ip AS INET), :digest, :patient_id)"
                ),
                {
                    "outcome": outcome,
                    "reason": reason,
                    "ip": str(patient["signup_ip"]) if patient["signup_ip"] else None,
                    "digest": digest,
                    "patient_id": str(patient["patient_id"]),
                },
            )
