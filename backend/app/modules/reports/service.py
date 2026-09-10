from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.prs.disease_scoring import compute_disease_composite
from app.modules.prs.repository import PrsScaleResultRepository
from app.modules.reports.repository import ReportsRepository

# Documents/Anava_Doctor_Portal_Analytics_Dashboard_Backend_Design_v1.docx
# Section 4.1 (Edge Case E19) — a proposed default, not derived from any
# existing clinical spec. Change this one constant to retune every KPI,
# chart, and the cohort overview at once; never duplicate this threshold
# elsewhere.
TREND_THRESHOLD_PCT = 0.20


def _classify_trend(baseline: float, latest: float) -> str:
    """Section 4.1's shared rule. Caller must already know there are >=2
    visits — a single-visit or zero-visit patient is "insufficient_data"
    and never reaches this function (baseline/latest wouldn't both exist)."""
    if baseline <= 0:
        # Already at the floor of the 0-100 scale — can't "improve" further
        # by percentage; any rise is worsening, anything else is stable.
        return "worsening" if latest > baseline else "stable"
    change_pct = (baseline - latest) / baseline
    if change_pct >= TREND_THRESHOLD_PCT:
        return "improving"
    if -change_pct >= TREND_THRESHOLD_PCT:
        return "worsening"
    return "stable"


def _group_by_patient(rows: list[dict]) -> list[dict]:
    """Raw joined rows (repository.doctor_patients_overview) -> one dict per
    patient with a chronological visits[] list. A patient row with no
    composite yet (composite_id NULL, from the LEFT JOIN) keeps an empty
    visits[] rather than a fake entry."""
    by_patient: dict = {}
    order: list = []
    for r in rows:
        pid = r["patient_id"]
        if pid not in by_patient:
            by_patient[pid] = {
                "patient_id": str(pid),
                "name": f"{r['first_name']} {r['last_name']}".strip(),
                "visits": [],
            }
            order.append(pid)
        if r["composite_id"] is not None:
            by_patient[pid]["visits"].append(
                {
                    "composite_id": str(r["composite_id"]),
                    "date": r["computed_at"],
                    "score": float(r["calculated_value"]),
                    "severity_level": r["severity_level"],
                    "severity_label": r["severity_label"],
                    "is_provisional": r["is_provisional"],
                }
            )
    return [by_patient[pid] for pid in order]


def compute_dashboard_overview(patients: list[dict]) -> dict:
    """Pure aggregation over already-grouped per-patient visit lists — no DB,
    unit-testable directly. Business rules per Documents/Anava_Doctor_
    Portal_Analytics_Dashboard_Backend_Design_v1.docx Sections 4-5.2:
      - baseline = the row disease_composite_scores flags is_baseline (the
        patient's first-ever completed composite for this disease — never
        resets on a new treatment protocol); visits are already ordered by
        computed_at, so visits[0] IS that row whenever one exists.
      - latest = the patient's most recent composite (visits[-1]).
      - overall_change = latest - baseline (negative = improvement, since
        every score here is direction-corrected so higher = worse).
      - trend: insufficient_data (<2 visits), else Section 4.1's rule.
      - responder = >=50% drop from baseline to latest (unchanged from the
        pre-Phase-3 definition — a stricter, separate KPI from "improving").
      - remitter = latest visit's severity_level == "normal".
      - provisional = latest visit's is_provisional flag.
    Responders/remitters/avg_score_change/provisional need a real latest
    visit; a patient with zero visits is counted in `patients` and shown in
    the table with an empty visit list, but excluded from every other KPI.
    """
    patient_rows = []
    changes: list[float] = []
    responders = 0
    remitters = 0
    provisional = 0
    comparable = 0
    assessment_counts: list[int] = []
    trend_counts = {"improving": 0, "stable": 0, "worsening": 0, "insufficient_data": 0}

    for p in patients:
        visits = p["visits"]  # already ordered by computed_at (repository ORDER BY)
        assessment_counts.append(len(visits))
        overall_change = None
        trend = "insufficient_data"

        if len(visits) >= 2:
            baseline, latest = visits[0], visits[-1]
            overall_change = round(latest["score"] - baseline["score"], 2)
            changes.append(overall_change)
            comparable += 1
            if baseline["score"] > 0 and (baseline["score"] - latest["score"]) / baseline["score"] >= 0.5:
                responders += 1
            trend = _classify_trend(baseline["score"], latest["score"])

        if visits:
            if visits[-1]["severity_level"] == "normal":
                remitters += 1
            if visits[-1]["is_provisional"]:
                provisional += 1

        trend_counts[trend] += 1
        patient_rows.append(
            {
                "patient_id": p["patient_id"],
                "name": p["name"],
                "first_assessment_date": visits[0]["date"] if visits else None,
                "assessment_count": len(visits),
                "overall_change": overall_change,
                "trend": trend,
                "visits": visits,
            }
        )

    total = len(patients)
    kpis = {
        "patients": total,
        "improving": trend_counts["improving"],
        "stable": trend_counts["stable"],
        "worsening": trend_counts["worsening"],
        "insufficient_data": trend_counts["insufficient_data"],
        "avg_assessments": round(sum(assessment_counts) / total, 2) if total else 0.0,
        "avg_score_change": round(sum(changes) / len(changes), 2) if changes else None,
        "responders_pct": round(responders / comparable * 100, 1) if comparable else None,
        "remitters_pct": round(remitters / total * 100, 1) if total else 0.0,
        "provisional_pct": round(provisional / total * 100, 1) if total else 0.0,
    }
    return {"kpis": kpis, "patients": patient_rows}


def compute_diseases_overview(rows: list[dict], total_patients: int) -> dict:
    """Raw joined rows (repository.doctor_diseases_overview) -> per-disease
    trend-bucket counts plus a cohort-wide summary card (Backend Design v1
    Section 5.1). Every active disease in the catalog appears — including
    ones with a NULL patient_id (zero of this doctor's patients have PRS
    data for it yet), which is why disease_name is read off the placeholder
    row rather than requiring a real composite to exist first. Reuses the
    exact same _classify_trend as the per-disease KPI panel.

    The summary counts (patient, disease) PAIRS, not distinct patients — a
    patient tracked under two diseases counts once per disease there. Only
    `total_patients` (passed in from a separate DISTINCT headcount query)
    is a true unique count; the rest intentionally mirror the per-disease
    breakdown's own units so the numbers agree with what's on the page
    right below the summary card.
    """
    by_disease: dict[str, dict] = {}
    disease_order: list[str] = []
    assessments_in_window = 0
    provisional_pending = 0

    for r in rows:
        did = r["disease_id"]
        if did not in by_disease:
            by_disease[did] = {"disease_name": r["disease_name"], "patients": {}}
            disease_order.append(did)
        if r["patient_id"] is None:
            continue  # placeholder row for a disease with zero scored patients
        patients = by_disease[did]["patients"]
        pid = r["patient_id"]
        if pid not in patients:
            patients[pid] = []
        patients[pid].append({"score": float(r["calculated_value"]), "is_baseline": r["is_baseline"]})
        assessments_in_window += 1
        if r.get("is_provisional"):
            provisional_pending += 1

    diseases = []
    total_improving = 0
    total_worsening = 0
    total_pairs = 0
    tracked_cohorts = 0
    for did in disease_order:
        entry = by_disease[did]
        counts = {"improving": 0, "stable": 0, "worsening": 0, "insufficient_data": 0}
        for visits in entry["patients"].values():
            if len(visits) < 2:
                counts["insufficient_data"] += 1
                continue
            baseline = next((v for v in visits if v["is_baseline"]), visits[0])
            latest = visits[-1]
            counts[_classify_trend(baseline["score"], latest["score"])] += 1
        total = len(entry["patients"])
        if total > 0:
            tracked_cohorts += 1
        total_improving += counts["improving"]
        total_worsening += counts["worsening"]
        total_pairs += total
        diseases.append({"disease_id": did, "disease_name": entry["disease_name"], "total": total, **counts})

    summary = {
        "total_patients": total_patients,
        "disease_cohorts": tracked_cohorts,
        "improving_pct": round(total_improving / total_pairs * 100, 1) if total_pairs else 0.0,
        "improving_patients": total_improving,
        "worsening_pct": round(total_worsening / total_pairs * 100, 1) if total_pairs else 0.0,
        "worsening_patients": total_worsening,
        "assessments_in_window": assessments_in_window,
        "provisional_pending": provisional_pending,
    }
    return {"summary": summary, "diseases": diseases}


def compute_scale_trajectories(rows: list[dict]) -> dict:
    """Raw joined rows (repository.patient_scale_trajectories) -> one entry
    per scale with its chronological point history (Backend Design v1
    Section 5.4). Rows already arrive ordered by (scale_code, completed_at)."""
    by_scale: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        code = r["scale_code"]
        if code not in by_scale:
            by_scale[code] = {"scale_code": code, "scale_name": r["scale_name"], "points": []}
            order.append(code)
        by_scale[code]["points"].append(
            {
                "date": r["date"],
                "score": float(r["score"]),
                "severity_level": r["severity_level"],
                "severity_label": r["severity_label"],
            }
        )
    return {"scales": [by_scale[c] for c in order]}


def _as_naive(dt) -> datetime | None:
    """Composite dates come back tz-aware from the DB (timestamptz) but as
    plain strings/naive datetimes in unit tests — normalize to naive so
    comparisons never raise."""
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def group_protocol_captures_by_patient(rows: list[dict], weights: dict[str, float]) -> list[dict]:
    """Raw rows (repository.doctor_protocol_scale_captures) -> one dict per
    patient with a chronological visits[] list, each visit being one device
    session's composite score (Section 5.7 — sourced from protocol-session
    PRS captures, not disease_composite_scores, since a treatment protocol's
    weekly check-ins are a distinct cadence from the main assessment flow).
    A device session may score several scales at once; they're grouped by
    (patient_id, recorded_at) and run through the same weighted composite
    formula every other composite in this system uses, so the numbers on
    this table are directly comparable to the rest of the dashboard."""
    by_patient: dict = {}
    order: list = []
    sessions: dict = {}  # (patient_id, recorded_at) -> scale_inputs
    session_order: dict = {}  # patient_id -> ordered list of recorded_at

    for r in rows:
        pid = r["patient_id"]
        if pid not in by_patient:
            by_patient[pid] = {"patient_id": str(pid), "name": f"{r['first_name']} {r['last_name']}".strip()}
            order.append(pid)
            session_order[pid] = []
        if r["recorded_at"] is None or r["scale_code"] is None or r["scale_code"] not in weights:
            continue
        key = (pid, r["recorded_at"])
        if key not in sessions:
            sessions[key] = []
            session_order[pid].append(r["recorded_at"])
        sessions[key].append({"scale_code": r["scale_code"], "percentage": float(r["percentage"]), "weight_pct": weights[r["scale_code"]]})

    patients = []
    for pid in order:
        visits = []
        for recorded_at in session_order[pid]:
            result = compute_disease_composite(sessions[(pid, recorded_at)])
            if result["composite_score"] is not None:
                visits.append({"date": recorded_at, "score": result["composite_score"]})
        patients.append({"patient_id": by_patient[pid]["patient_id"], "name": by_patient[pid]["name"], "visits": visits})
    return patients


def compute_weekly_trend(patients: list[dict], weeks: int, as_of: datetime | None = None) -> dict:
    """Section 5.7 — one row per patient, one column per ISO week, carrying
    the most recent composite forward into any week with no new assessment
    (so a patient scored once stays visible in every later week, not just
    the week they happened to test). A week strictly before a patient's
    first visit is null, never a fabricated 0."""
    as_of = _as_naive(as_of) or datetime.utcnow()
    monday_this_week = (as_of - timedelta(days=as_of.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_starts = [monday_this_week - timedelta(weeks=(weeks - 1 - i)) for i in range(weeks)]

    patient_rows = []
    for p in patients:
        visits = sorted(
            ({"date": _as_naive(v["date"]), "score": v["score"]} for v in p["visits"] if v["date"] is not None),
            key=lambda v: v["date"],
        )
        scores: list[float | None] = []
        for ws in week_starts:
            week_end = ws + timedelta(weeks=1)
            candidates = [v for v in visits if v["date"] < week_end]
            scores.append(candidates[-1]["score"] if candidates else None)
        patient_rows.append({"patient_id": p["patient_id"], "name": p["name"], "scores": scores})

    return {"weeks": week_starts, "patients": patient_rows}


def compute_protocol_outcomes(rows: list[dict], weights: dict[str, float]) -> dict:
    """Raw per-scale/per-session rows (repository.doctor_protocol_scale_
    history) -> Section 5.5 (composite trend per protocol) + 5.6 (protocol
    x scale heatmap), computed together since both start from the same
    session-grouped data.

    5.5 reuses the identical weighted-composite-per-session math as the
    Weekly Trend table (group_protocol_captures_by_patient) — just grouped
    by (patient, protocol) instead of (patient, week) — then classifies
    each patient's first-session-on-this-protocol -> last-session trend
    with the same _classify_trend rule as everywhere else.

    5.6 skips the composite entirely: a cell is one scale's own raw
    percentage change (first -> last capture on that protocol), averaged
    across every patient who has >=2 captures of that scale on that
    protocol. Two different questions - "did the patient improve overall
    on this protocol" vs "did this specific scale move under this
    protocol" - so they're deliberately not the same math.
    """
    sessions: dict[tuple, list] = {}  # (patient_id, protocol_label, recorded_at) -> scale_inputs
    session_dates: dict[tuple, set] = {}  # (patient_id, protocol_label) -> {recorded_at, ...}
    scale_points: dict[tuple, list] = {}  # (patient_id, protocol_label, scale_code) -> [(recorded_at, pct), ...]
    scale_names: dict[str, str] = {}

    for r in rows:
        pid, label, code, name, pct, date = (
            r["patient_id"],
            r["protocol_label"],
            r["scale_code"],
            r["scale_name"],
            r["percentage"],
            r["recorded_at"],
        )
        scale_names[code] = name
        session_dates.setdefault((pid, label), set()).add(date)
        weight = weights.get(code, 0)
        if weight > 0:
            sessions.setdefault((pid, label, date), []).append({"scale_code": code, "percentage": float(pct), "weight_pct": weight})
        scale_points.setdefault((pid, label, code), []).append((date, float(pct)))

    # -- 5.5: composite trend per protocol --
    protocol_patients: dict[str, dict[str, list]] = {}
    for (pid, label), dates in session_dates.items():
        visits = []
        for date in sorted(dates):
            result = compute_disease_composite(sessions.get((pid, label, date), []))
            if result["composite_score"] is not None:
                visits.append(result["composite_score"])
        protocol_patients.setdefault(label, {})[pid] = visits

    protocols: list[dict] = []
    for label, patients in protocol_patients.items():
        counts = {"improving": 0, "stable": 0, "worsening": 0, "insufficient_data": 0}
        for visits in patients.values():
            if len(visits) < 2:
                counts["insufficient_data"] += 1
                continue
            counts[_classify_trend(visits[0], visits[-1])] += 1
        protocols.append({"protocol_label": label, "total": len(patients), **counts})
    protocols.sort(key=lambda p: int(p["total"]), reverse=True)

    # -- 5.6: protocol x scale heatmap --
    heatmap_acc: dict[tuple, list] = {}
    for (_pid, label, code), points in scale_points.items():
        ordered = sorted(points)
        if len(ordered) < 2:
            continue
        change = round(ordered[-1][1] - ordered[0][1], 2)
        heatmap_acc.setdefault((label, code), []).append(change)

    heatmap = [
        {
            "protocol_label": label,
            "scale_code": code,
            "scale_name": scale_names[code],
            "avg_change": round(sum(changes) / len(changes), 2),
            "n": len(changes),
        }
        for (label, code), changes in heatmap_acc.items()
    ]

    return {"protocols": protocols, "heatmap": heatmap}


class ReportsService:
    def __init__(self, session: AsyncSession):
        self.repo = ReportsRepository(session)
        self.scale_results = PrsScaleResultRepository(session)

    async def doctor_patients_overview(self, doctor_profile_id: UUID, disease_id: str) -> dict:
        rows = await self.repo.doctor_patients_overview(doctor_profile_id, disease_id)
        patients = _group_by_patient(rows)
        return compute_dashboard_overview(patients)

    async def doctor_diseases_overview(self, doctor_profile_id: UUID) -> dict:
        rows = await self.repo.doctor_diseases_overview(doctor_profile_id)
        total_patients = await self.repo.doctor_active_patient_count(doctor_profile_id)
        return compute_diseases_overview(rows, total_patients)

    async def patient_scale_trajectories(self, doctor_profile_id: UUID, patient_id: UUID, disease_id: str) -> dict:
        rows = await self.repo.patient_scale_trajectories(doctor_profile_id, patient_id, disease_id)
        return compute_scale_trajectories(rows)

    async def doctor_weekly_trend(self, doctor_profile_id: UUID, disease_id: str, weeks: int) -> dict:
        formula = await self.scale_results.active_disease_formula(disease_id)
        weights: dict[str, float] = formula["config"]["weights"] if formula else {}
        rows = await self.repo.doctor_protocol_scale_captures(doctor_profile_id, disease_id)
        patients = group_protocol_captures_by_patient(rows, weights)
        return compute_weekly_trend(patients, weeks)

    async def doctor_protocol_outcomes(self, doctor_profile_id: UUID, disease_id: str) -> dict:
        formula = await self.scale_results.active_disease_formula(disease_id)
        weights: dict[str, float] = formula["config"]["weights"] if formula else {}
        rows = await self.repo.doctor_protocol_scale_history(doctor_profile_id, disease_id)
        return compute_protocol_outcomes(rows, weights)
