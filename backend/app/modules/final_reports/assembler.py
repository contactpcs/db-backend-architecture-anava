"""Pure section builders for the Final Report — no DB, unit-testable directly
(tests/unit/test_final_report_assembler.py). The service feeds these raw rows
from repository.py and stores what they return as the frozen snapshot.
See Documents/Anava_Final_Report_Plan_v1.md section 3 for the section map."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from app.modules.prs.disease_scoring import compute_disease_composite
from app.modules.reports.service import compute_weekly_trend

SCOPES = ("overall", "last_consultation")
# Rows printed per block: the last 3 main PRS of a condition, the last 5
# sessions of a protocol instance (owner, 2026-10-13).
PRS_ENTRIES_PER_DISEASE = 3
PRS_SESSIONS_PER_INSTANCE = 5
PROGRESS_WEEKS = 5
# Crash guard only. A PRS with no disease (disease_id IS NULL) is the general
# registration assessment, which repository.prs_instances no longer reads —
# but a row without a disease name must never break the sort again.
GENERAL_PRS_NAME = "General assessment"
# Not the patient's treatment plan: never prescribed (draft, cancelled), or
# an older version replaced by an amendment (superseded — its sessions still
# show under Session Details, and the version that replaced it is listed).
_NOT_THE_PLAN_STATUSES = {"draft", "cancelled", "superseded"}
_DEVIATION_FLAGS = (
    ("intensity_deviates", "intensity"),
    ("duration_deviates", "duration"),
    ("ramp_up_deviates", "ramp up"),
    ("ramp_down_deviates", "ramp down"),
)


def _naive(value) -> datetime | None:
    """timestamptz comes back tz-aware from the DB and naive in unit tests —
    normalize so window comparisons never raise."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):  # a plain date
        value = datetime(value.year, value.month, value.day)
    return value.replace(tzinfo=None) if value.tzinfo else value


def in_window(value, start, end) -> bool:
    """start is exclusive, end inclusive. A None bound is open, so the
    `overall` scope (None, None) keeps everything; a row with no timestamp
    only survives an open window."""
    if start is None and end is None:
        return True
    ts = _naive(value)
    if ts is None:
        return False
    start_ts = _naive(start)
    end_ts = _naive(end)
    if start_ts is not None and ts <= start_ts:
        return False
    return end_ts is None or ts <= end_ts


def consultation_window(consultations: list[dict], now) -> dict:
    """`last_consultation` scope: from the previous started consultation to the
    end of the last one (its completed_at, or now while it is still open).
    consultations arrive oldest first. Empty list -> no consultation."""
    if not consultations:
        return {"consultation": None, "start": None, "end": None}
    last = consultations[-1]
    previous = consultations[-2] if len(consultations) > 1 else None
    return {
        "consultation": last,
        "start": previous["started_at"] if previous else None,
        "end": last.get("completed_at") or now,
    }


def build_anamnesis(assessment: dict, question_rows: list[dict], option_labels: dict[tuple, str]) -> dict:
    """Every question of the assessment's catalogue with its answer, grouped
    by section — unanswered ones included with answer None (plan decision 5:
    print all questions). option_labels maps (question_id, option_value) to
    the label the patient actually saw."""
    sections: dict = {}
    for q in question_rows:
        raw = q["response_values"] if q.get("response_values") else ([q["response_value"]] if q.get("response_value") else [])
        answers = [option_labels.get((q["question_id"], v), v) for v in raw]
        sections.setdefault((q["section_number"], q["section_title"]), []).append(
            {"question": q["question_text"], "answer": ", ".join(answers) if answers else None}
        )
    return {
        "anamnesis_id": assessment["anamnesis_id"],
        "stage": assessment["assessment_stage"],
        "status": assessment["status"],
        "appointment_id": assessment.get("appointment_id"),
        "recorded_at": assessment.get("completed_at") or assessment.get("created_at"),
        "sections": [{"title": title, "items": items} for (_, title), items in sorted(sections.items(), key=lambda kv: kv[0][0] or 0)],
    }


def _origin(row: dict) -> str:
    """Where a PRS was taken: decided by the appointment the PRS itself was
    started at, nothing else. A device session can also point at an older PRS
    that was not taken there (device_session_prs_responses / _scales linking
    a standalone main PRS); that link does not make it a device-session PRS —
    it stays the main PRS of its condition."""
    return row.get("appointment_type") or row.get("assessment_stage") or "assessment"


def _scales_as_of(taken_at, own: list[dict], history: dict[str, list[dict]], codes: set[str], own_instances: set[str]) -> list[dict]:
    """Every scale in `codes` as it stood at `taken_at`: the scales answered in
    this sitting (`own`), plus — for each other scale — the patient's most
    recent earlier result, marked `carried` with the date it was taken. Same
    as-of-latest-per-scale rule the disease composite uses
    (prs/repository.py asof_scale_values_for_disease), evaluated at this
    sitting's date, so a later result never leaks into an earlier row."""
    taken = _naive(taken_at)
    by_code: dict[str, dict] = {}
    for s in sorted(own, key=lambda s: (s["_at"] is not None, s["_at"] or datetime.min)):  # latest answer of a scale wins
        by_code[s["scale_code"]] = {**s, "carried": False, "taken_at": s["_taken_at"] or taken_at}
    for code in codes - set(by_code):
        earlier = next(
            (
                s
                for s in history.get(code, [])  # newest first
                if s["instance_id"] not in own_instances and taken is not None and s["_at"] is not None and s["_at"] <= taken
            ),
            None,
        )
        if earlier:
            by_code[code] = {**earlier, "carried": True, "taken_at": earlier["_taken_at"]}
    return [by_code[code] for code in sorted(by_code)]


def _scale_out(s: dict) -> dict:
    return {
        "scale_code": s["scale_code"],
        "scale_name": s.get("scale_name") or s["scale_code"],
        "value": s.get("calculated_value"),
        "max": s.get("max_possible"),
        "percentage": s.get("percentage"),
        "severity_label": s.get("severity_label"),
        # True = not answered in this sitting: the latest earlier result,
        # taken on taken_at.
        "carried": s["carried"],
        "taken_at": s["taken_at"],
    }


def _session_score(scales: list[dict]) -> dict:
    """One number for a device session, so its progress can be followed week
    to week: the plain average of its scales' direction-corrected percentages
    (0-100, higher = worse — the same per-scale value every composite uses).
    Equal weights on purpose: a protocol prescribes individual scales, so no
    condition's weight formula applies. Scales without a stored percentage are
    left out; none at all gives no score."""
    return compute_disease_composite(
        [
            {"scale_code": s["scale_code"], "percentage": float(s["direction_corrected_percentage"]), "weight_pct": 1}
            for s in scales
            if s.get("direction_corrected_percentage") is not None
        ]
    )


def _device_session_groups(
    rows: list[dict],
    scales_by_instance: dict[str, list[dict]],
    history: dict[str, list[dict]],
    conditions_by_protocol: dict[str, list[str]],
    protocol_instances: list[dict],
    *,
    window_start,
    window_end,
    now,
) -> list[dict]:
    """PRS taken at device sessions, one row per SESSION, one block per
    PROTOCOL INSTANCE (a patient can run several devices in parallel, SQL 90):
    "Instance 1", "Instance 2", ... Every instance in `protocol_instances`
    (the report's treatment protocols) gets a block, also when it has no PRS
    yet, so the report shows all the devices the patient is on.

    They are deliberately NOT grouped by prs_assessment_instances.disease_id.
    A protocol prescribes individual scales, and when one is opened the
    frontend files it under the first catalogue disease, alphabetically, that
    contains the scale (prsAssessment.service.ts resolveDiseaseAndScaleId) —
    BDI-II lands under "ALS" for a depression protocol. That label says
    nothing about the patient, so it is not printed."""
    sessions: dict = {}  # appointment (or lone instance) -> its PRS rows
    for row in rows:
        sessions.setdefault(row.get("appointment_id") or row["instance_id"], []).append(row)

    groups: dict = {}
    for inst in protocol_instances:
        groups[str(inst["instance_id"])] = {
            "instance_number": inst.get("instance_number"),
            "device_name": inst.get("device_name"),
            "conditions": inst.get("conditions") or [],
            "sessions": [],
            "listed": True,
        }
    for key, parts in sessions.items():
        latest = max(parts, key=lambda r: (_naive(r["completed_at"]) is not None, _naive(r["completed_at"]) or datetime.min))
        first = next((r for r in parts if r.get("protocol_id")), parts[0])
        device_name = next((r.get("device_name") for r in parts if r.get("device_name")), None)
        conditions = conditions_by_protocol.get(str(first.get("protocol_id")), []) if first.get("protocol_id") else []
        # Versions of one protocol share its instance. A session with no
        # instance on record falls back to its device + conditions.
        group_key = str(first["protocol_instance_id"]) if first.get("protocol_instance_id") else (device_name, tuple(conditions))
        group = groups.setdefault(
            group_key,
            {
                "instance_number": first.get("instance_number"),
                "device_name": device_name,
                "conditions": conditions,
                "sessions": [],
                "listed": False,
            },
        )
        group["sessions"].append(
            {
                "appointment_id": key,
                "completed_at": latest["completed_at"],
                "session_number": next((r.get("session_number") for r in parts if r.get("session_number") is not None), None),
                "instances": {r["instance_id"] for r in parts},
                "own": [s for r in parts for s in scales_by_instance.get(r["instance_id"], [])],
            }
        )

    out = []
    for group in groups.values():
        newest_first = sorted(
            group.pop("sessions"),
            key=lambda x: (_naive(x["completed_at"]) is not None, _naive(x["completed_at"]) or datetime.min),
            reverse=True,
        )
        listed = group.pop("listed")
        # "All scales": every scale answered at any session of this instance.
        codes = {s["scale_code"] for x in newest_first for s in x["own"]}
        entries, scored = [], []
        for x in newest_first:
            scales = _scales_as_of(x["completed_at"], x["own"], history, codes, x["instances"])
            score = _session_score(scales)
            if score["composite_score"] is not None:
                scored.append({"date": x["completed_at"], "score": score["composite_score"]})
            if not in_window(x["completed_at"], window_start, window_end):
                continue
            entries.append(
                {
                    "appointment_id": x["appointment_id"],
                    "completed_at": x["completed_at"],
                    "session_number": x["session_number"],
                    "score": score["composite_score"],
                    "severity_level": score["severity_level"],
                    "severity_label": score["severity_label"],
                    "scales": [_scale_out(s) for s in scales],
                }
            )
        if not entries and not listed:
            continue  # an old instance with nothing in this report's window
        trend = compute_weekly_trend([{"patient_id": "", "name": "", "visits": scored}], PROGRESS_WEEKS, as_of=now)
        out.append(
            {
                **group,
                "entries": entries[:PRS_SESSIONS_PER_INSTANCE],
                "weekly_progress": {"weeks": trend["weeks"], "scores": trend["patients"][0]["scores"]},
            }
        )
    return sorted(out, key=lambda g: (g["instance_number"] is None, g["instance_number"] or 0, g["device_name"] or ""))


def build_prs(
    instance_rows: list[dict],
    scale_rows: list[dict],
    weights_by_disease: dict[str, dict[str, float]],
    conditions_by_protocol: dict[str, list[str]],
    protocol_instances: list[dict] | None = None,
    *,
    window_start=None,
    window_end=None,
    now=None,
) -> dict:
    """Two kinds of PRS, reported separately.

    `diseases` — the main PRS (consultation or standalone), which the doctor
    assigned for a condition. Last PRS_ENTRIES_PER_DISEASE per condition, each
    with every scale of the condition and a weighted overall score, plus one
    PROGRESS_WEEKS-week progress row. A sitting may answer only some scales;
    its row still lists ALL of them: the ones answered then, and the latest
    earlier value of each other one (see _scales_as_of). The overall score
    runs those through the same weighted formula as every other composite in
    the system (compute_disease_composite), so each row is the patient's
    composite as of that sitting. A scale with no weight for the condition is
    listed but does not move the score.

    `device_sessions` — PRS taken at device sessions, one block per protocol
    instance and no condition (see _device_session_groups for why). Its
    overall score is the plain average of the scales (_session_score).

    instance_rows are ALL of the patient's reportable PRS, newest first: the
    window limits which sittings get a row, not which earlier results a row
    may carry forward."""
    disease_of = {row["instance_id"]: row["disease_id"] for row in instance_rows}
    taken_at = {row["instance_id"]: row["completed_at"] for row in instance_rows}
    main_instances = {row["instance_id"] for row in instance_rows if _origin(row) != "device_session"}
    scales_by_instance: dict[str, list[dict]] = {}
    history: dict[str, list[dict]] = {}  # scale_code -> its results, newest first
    codes_of_disease: dict[str | None, set[str]] = {}
    for sr in scale_rows:
        # A result whose scale left the catalogue still has its id ("PHQ9/2026").
        code = sr.get("scale_code") or str(sr.get("scale_id") or "").split("/")[0]
        at = taken_at.get(sr["instance_id"])
        result = {**sr, "scale_code": code, "_at": _naive(at), "_taken_at": at}
        scales_by_instance.setdefault(sr["instance_id"], []).append(result)
        history.setdefault(code, []).append(result)
        # The disease a device-session scale was filed under is arbitrary: it
        # must not add scales to that condition's rows.
        if sr["instance_id"] in main_instances:
            codes_of_disease.setdefault(disease_of.get(sr["instance_id"]), set()).add(code)
    for results in history.values():
        results.sort(key=lambda s: (s["_at"] is not None, s["_at"] or datetime.min, s["instance_id"]), reverse=True)

    by_disease: dict[str, dict] = {}
    for row in instance_rows:  # newest first
        if row["instance_id"] not in main_instances or not in_window(row["completed_at"], window_start, window_end):
            continue
        weights = weights_by_disease.get(row["disease_id"], {})
        scales = _scales_as_of(
            row["completed_at"],
            scales_by_instance.get(row["instance_id"], []),
            history,
            # The condition's weighted scales, and any other scale answered under it.
            set(weights) | codes_of_disease.get(row["disease_id"], set()),
            {row["instance_id"]},
        )
        composite = compute_disease_composite(
            [
                {
                    "scale_code": s["scale_code"],
                    "percentage": float(s["direction_corrected_percentage"]),
                    "weight_pct": weights[s["scale_code"]],
                }
                for s in scales
                if s.get("direction_corrected_percentage") is not None and weights.get(s["scale_code"], 0) > 0
            ]
        )
        entry = {
            "instance_id": row["instance_id"],
            "completed_at": row["completed_at"],
            "origin": _origin(row),
            "score": composite["composite_score"],
            "severity_level": composite["severity_level"],
            "severity_label": composite["severity_label"],
            # Kept for reports signed before device sessions got their own block.
            "device_name": None,
            "session_number": None,
            "conditions": [],
            "scales": [_scale_out(s) for s in scales],
        }
        disease = by_disease.setdefault(
            row["disease_id"],
            {"disease_id": row["disease_id"], "disease_name": row.get("disease_name") or row["disease_id"] or GENERAL_PRS_NAME, "all": []},
        )
        disease["all"].append(entry)

    diseases = []
    for disease in by_disease.values():
        entries = disease.pop("all")
        scored = [{"date": e["completed_at"], "score": e["score"]} for e in entries if e["score"] is not None]
        trend = compute_weekly_trend([{"patient_id": "", "name": "", "visits": scored}], PROGRESS_WEEKS, as_of=now)
        disease["entries"] = entries[:PRS_ENTRIES_PER_DISEASE]
        disease["weekly_progress"] = {"weeks": trend["weeks"], "scores": trend["patients"][0]["scores"]}
        diseases.append(disease)
    return {
        "diseases": sorted(diseases, key=lambda d: d["disease_name"]),
        "device_sessions": _device_session_groups(
            [row for row in instance_rows if row["instance_id"] not in main_instances],
            scales_by_instance,
            history,
            conditions_by_protocol,
            protocol_instances or [],
            window_start=window_start,
            window_end=window_end,
            now=now,
        ),
    }


def select_notes(notes: list[dict], note_ids: list[str]) -> list[dict]:
    """The notes the doctor ticked, newest first. Raises KeyError naming the
    first id that is not one of this patient's notes — the service turns that
    into a 403 rather than silently dropping it."""
    by_id = {str(n["note_id"]): n for n in notes}
    for nid in note_ids:
        if str(nid) not in by_id:
            raise KeyError(str(nid))
    wanted = {str(n) for n in note_ids}
    return [
        {
            "note_id": n["note_id"],
            "category": n["category"],
            "note_text": n["note_text"],
            "created_at": n["created_at"],
            "doctor_name": n.get("doctor_name"),
        }
        for n in notes
        if str(n["note_id"]) in wanted
    ]


def build_medications(rows: list[dict]) -> list[dict]:
    """Full history, active first then stopped (plan decision 7)."""
    keys = (
        "medicine_name",
        "dose",
        "timing",
        "meal_instruction",
        "duration",
        "note",
        "status",
        "started_at",
        "stopped_at",
        "prescribed_by_name",
    )
    return [{k: r.get(k) for k in keys} for r in sorted(rows, key=lambda r: r["status"] != "active")]


def pick_protocols(protocols: list[dict], scope: str) -> list[dict]:
    """overall: every protocol that is or was the plan (active, completed).
    last_consultation: the active one(s) — several can run in parallel
    (SQL 90) — falling back to the most recent completed one. protocols
    arrive newest first."""
    plans = [p for p in protocols if p["status"] not in _NOT_THE_PLAN_STATUSES]
    if scope == "overall":
        return plans
    return [p for p in plans if p["status"] == "active"] or plans[:1]


def build_treatment_plan(detail: dict) -> dict:
    """Placement + dosage + schedule from ProtocolService.get_detail()."""
    montage = detail.get("custom_montage")
    return {
        "protocol_id": detail["protocol_id"],
        "instance_number": detail.get("instance_number"),
        "status": detail["status"],
        "version": f"{detail.get('version_major', 1)}.{detail.get('version_minor', 0)}",
        "device_name": detail.get("device_name"),
        "modality": detail.get("modality"),
        "prescribed_by": detail.get("doctor_name"),
        "activated_at": detail.get("activated_at"),
        "conditions": [c.get("condition_name") or c.get("other_text") for c in detail.get("conditions", [])],
        "placement": montage["montage_name"] if montage else detail.get("placement_summary"),
        "dosage": {
            k: detail.get(k)
            for k in (
                "prescribed_current_ma",
                "prescribed_duration_min",
                "ramp_seconds",
                "prescribed_tvns_wavelength",
                "prescribed_tvns_pattern",
                "prescribed_tvns_strength_pct",
                "prescribed_tvns_frequency_hz",
                "prescribed_tvns_pulse_width_us",
                "prescribed_tvns_duration_min",
            )
            if detail.get(k) is not None
        },
        "session_count": detail.get("session_count"),
        "sessions_per_week": detail.get("sessions_per_week"),
        "follow_up_every_n": detail.get("follow_up_every_n"),
        "schedule": [
            {
                "session_number": s.get("session_number"),
                "date": s["appointment_date"],
                "start_time": s.get("start_time"),
                "status": s["status"],
            }
            for s in detail.get("sessions", [])
        ],
        "follow_ups": [
            {"after_session_number": s.get("session_number"), "date": s["appointment_date"], "status": s["status"]}
            for s in detail.get("follow_ups", [])
        ],
    }


def protocol_instances(details: list[dict]) -> list[dict]:
    """The protocol instances behind the report's treatment protocols, one
    entry each (several versions of a protocol share an instance)."""
    seen: dict[str, dict] = {}
    for detail in details:
        if detail.get("instance_id") is None:
            continue
        seen.setdefault(
            str(detail["instance_id"]),
            {
                "instance_id": detail["instance_id"],
                "instance_number": detail.get("instance_number"),
                "device_name": detail.get("device_name"),
                "conditions": [c.get("condition_name") or c.get("other_text") for c in detail.get("conditions", [])],
            },
        )
    return list(seen.values())


def diagnosis_codes(details: list[dict]) -> list[dict]:
    """ICD-10 codes picked in the protocol wizard, de-duplicated — printed
    read-only under the doctor's typed diagnosis (plan Q-A)."""
    seen: dict[str, dict] = {}
    for detail in details:
        for d in detail.get("diagnoses", []):
            seen.setdefault(d["icd10_code"], {"icd10_code": d["icd10_code"], "icd10_description": d.get("icd10_description")})
    return sorted(seen.values(), key=lambda d: d["icd10_code"])


def build_sessions(rows: list[dict], *, window_start=None, window_end=None) -> list[dict]:
    """Done device sessions, oldest first: what was prescribed, what was
    delivered, which parameters deviated, and symptom / adverse-event counts.
    The CA's free-text notes are internal and deliberately left out (plan Q-C)."""
    out = []
    for r in rows:
        if not in_window(r.get("completed_at") or r.get("stopped_at") or r.get("started_at"), window_start, window_end):
            continue
        out.append(
            {
                "session_number": r.get("session_number"),
                "date": r["appointment_date"],
                "status": r["session_status"],
                "device_name": r.get("device_name"),
                "prescribed_current_ma": r.get("prescribed_current_ma"),
                "actual_intensity_ma": r.get("actual_intensity_ma"),
                "prescribed_duration_min": r.get("prescribed_duration_min"),
                "actual_duration_min": r.get("actual_duration_min"),
                "deviations": [label for flag, label in _DEVIATION_FLAGS if r.get(flag)],
                "stop_reason": r.get("pause_stop_reason") if r["session_status"] == "stopped_early" else None,
                "symptom_count": r.get("symptom_count", 0),
                "adverse_event_count": r.get("adverse_event_count", 0),
            }
        )
    return out


def content_hash(diagnosis: str, snapshot: dict, signature: dict) -> str:
    """sha256 over the canonical JSON of what the doctor signed. Inputs must
    already be JSON-safe (the service runs them through jsonable_encoder)."""
    canonical = json.dumps({"diagnosis": diagnosis, "snapshot": snapshot, "signature": signature}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
