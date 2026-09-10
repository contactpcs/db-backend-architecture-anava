"""Doctor dashboard KPI math — pure aggregation, no database.

Business rules from Documents/Anava_Doctor_Portal_Analytics_Dashboard_
Backend_Design_v1.docx Sections 4-5.2: baseline = first completed composite
for the disease, latest = most recent, responder = >=50% drop
baseline->latest, remitter = latest severity_level == "normal", trend =
Section 4.1's +/-20% classification (insufficient_data below 2 visits).
"""

from datetime import datetime

from app.modules.reports.service import (
    compute_dashboard_overview,
    compute_diseases_overview,
    compute_protocol_outcomes,
    compute_scale_trajectories,
    compute_weekly_trend,
    group_protocol_captures_by_patient,
)


def _visit(score, severity_level="moderate", date="2026-01-01", provisional=False):
    return {
        "composite_id": "x",
        "date": date,
        "score": score,
        "severity_level": severity_level,
        "severity_label": severity_level.title(),
        "is_provisional": provisional,
    }


def test_responder_is_50pct_or_more_drop_from_baseline():
    patients = [
        {
            "patient_id": "p1",
            "name": "A",
            "visits": [_visit(80, date="2026-01-01"), _visit(35, "normal", date="2026-02-01")],
        }
    ]
    result = compute_dashboard_overview(patients)
    assert result["kpis"]["responders_pct"] == 100.0
    assert result["kpis"]["remitters_pct"] == 100.0
    assert result["patients"][0]["overall_change"] == -45.0


def test_less_than_50pct_drop_is_not_a_responder():
    patients = [{"patient_id": "p1", "name": "A", "visits": [_visit(80), _visit(50, date="2026-02-01")]}]
    result = compute_dashboard_overview(patients)
    assert result["kpis"]["responders_pct"] == 0.0


def test_single_visit_excluded_from_change_and_responder_math():
    patients = [{"patient_id": "p1", "name": "A", "visits": [_visit(60)]}]
    result = compute_dashboard_overview(patients)
    assert result["kpis"]["avg_score_change"] is None
    assert result["kpis"]["responders_pct"] is None
    assert result["patients"][0]["overall_change"] is None
    assert result["patients"][0]["assessment_count"] == 1


def test_patient_with_zero_visits_still_counted_in_patients_kpi():
    patients = [
        {"patient_id": "p1", "name": "A", "visits": [_visit(60), _visit(30, "normal", date="2026-02-01")]},
        {"patient_id": "p2", "name": "B", "visits": []},
    ]
    result = compute_dashboard_overview(patients)
    assert result["kpis"]["patients"] == 2
    assert result["kpis"]["remitters_pct"] == 50.0  # only p1 reached normal
    row_b = next(r for r in result["patients"] if r["patient_id"] == "p2")
    assert row_b["assessment_count"] == 0
    assert row_b["first_assessment_date"] is None


def test_worsening_patient_gets_positive_change_and_no_responder_credit():
    patients = [{"patient_id": "p1", "name": "A", "visits": [_visit(20), _visit(60, date="2026-02-01")]}]
    result = compute_dashboard_overview(patients)
    assert result["patients"][0]["overall_change"] == 40.0
    assert result["kpis"]["responders_pct"] == 0.0
    assert result["kpis"]["avg_score_change"] == 40.0


def test_zero_baseline_does_not_divide_by_zero():
    patients = [{"patient_id": "p1", "name": "A", "visits": [_visit(0), _visit(10, date="2026-02-01")]}]
    result = compute_dashboard_overview(patients)
    assert result["kpis"]["responders_pct"] == 0.0


def test_avg_assessments_averages_across_full_cohort_including_zero_visit_patients():
    patients = [
        {"patient_id": "p1", "name": "A", "visits": [_visit(60), _visit(30, date="2026-02-01")]},
        {"patient_id": "p2", "name": "B", "visits": []},
    ]
    result = compute_dashboard_overview(patients)
    assert result["kpis"]["avg_assessments"] == 1.0  # (2 + 0) / 2


def test_trend_buckets_and_insufficient_data():
    patients = [
        # >=20% drop -> improving
        {"patient_id": "p1", "name": "A", "visits": [_visit(80), _visit(60, date="2026-02-01")]},
        # within +/-20% band -> stable
        {"patient_id": "p2", "name": "B", "visits": [_visit(50), _visit(55, date="2026-02-01")]},
        # >=20% rise -> worsening
        {"patient_id": "p3", "name": "C", "visits": [_visit(40), _visit(60, date="2026-02-01")]},
        # <2 visits -> insufficient_data, never silently "stable"
        {"patient_id": "p4", "name": "D", "visits": [_visit(30)]},
    ]
    result = compute_dashboard_overview(patients)
    assert result["kpis"]["improving"] == 1
    assert result["kpis"]["stable"] == 1
    assert result["kpis"]["worsening"] == 1
    assert result["kpis"]["insufficient_data"] == 1
    trends = {r["patient_id"]: r["trend"] for r in result["patients"]}
    assert trends == {"p1": "improving", "p2": "stable", "p3": "worsening", "p4": "insufficient_data"}


def test_provisional_pct_counts_patients_whose_latest_visit_is_provisional():
    patients = [
        {"patient_id": "p1", "name": "A", "visits": [_visit(80), _visit(60, date="2026-02-01", provisional=True)]},
        {"patient_id": "p2", "name": "B", "visits": [_visit(50)]},
    ]
    result = compute_dashboard_overview(patients)
    assert result["kpis"]["provisional_pct"] == 50.0


def test_diseases_overview_groups_and_classifies_per_disease():
    rows = [
        {"disease_id": "ADHD", "disease_name": "ADHD", "patient_id": "p1", "calculated_value": 80.0, "is_baseline": True, "is_provisional": False},
        {"disease_id": "ADHD", "disease_name": "ADHD", "patient_id": "p1", "calculated_value": 55.0, "is_baseline": False, "is_provisional": True},
        {"disease_id": "ADHD", "disease_name": "ADHD", "patient_id": "p2", "calculated_value": 40.0, "is_baseline": True, "is_provisional": False},
        {"disease_id": "PAIN", "disease_name": "Chronic Pain", "patient_id": "p3", "calculated_value": 60.0, "is_baseline": True, "is_provisional": False},
    ]
    result = compute_diseases_overview(rows, total_patients=3)
    by_id = {d["disease_id"]: d for d in result["diseases"]}
    assert by_id["ADHD"]["total"] == 2
    assert by_id["ADHD"]["improving"] == 1  # p1: 80 -> 55, >=20% drop
    assert by_id["ADHD"]["insufficient_data"] == 1  # p2: only 1 row
    assert by_id["PAIN"]["total"] == 1
    assert by_id["PAIN"]["insufficient_data"] == 1

    summary = result["summary"]
    assert summary["total_patients"] == 3
    assert summary["disease_cohorts"] == 2
    assert summary["assessments_in_window"] == 4  # every composite row across both diseases
    assert summary["provisional_pending"] == 1
    assert summary["improving_patients"] == 1
    assert summary["improving_pct"] == 33.3  # 1 improving / 3 total (patient,disease) pairs


def test_scale_trajectories_groups_by_scale_in_arrival_order():
    rows = [
        {
            "scale_code": "BDI-II",
            "scale_name": "Beck Depression Inventory",
            "score": 40,
            "severity_level": "moderate",
            "severity_label": "Moderate",
            "date": "2026-01-01",
        },
        {
            "scale_code": "BDI-II",
            "scale_name": "Beck Depression Inventory",
            "score": 25,
            "severity_level": "mild",
            "severity_label": "Mild",
            "date": "2026-02-01",
        },
        {
            "scale_code": "GAD-7",
            "scale_name": "Generalized Anxiety Disorder 7",
            "score": 60,
            "severity_level": "moderate",
            "severity_label": "Moderate",
            "date": "2026-01-01",
        },
    ]
    result = compute_scale_trajectories(rows)
    by_code = {s["scale_code"]: s for s in result["scales"]}
    assert list(by_code) == ["BDI-II", "GAD-7"]
    assert [p["score"] for p in by_code["BDI-II"]["points"]] == [40.0, 25.0]
    assert len(by_code["GAD-7"]["points"]) == 1


def test_weekly_trend_carries_forward_last_score_into_empty_weeks():
    # Monday 2026-01-19; ask for 3 weeks ending that week.
    patients = [
        {
            "patient_id": "p1",
            "name": "A",
            "visits": [
                {"date": "2026-01-05", "score": 80.0},  # week of Jan 5
                # no visit the week of Jan 12 -> should carry 80 forward
                {"date": "2026-01-20", "score": 50.0},  # week of Jan 19
            ],
        },
        {"patient_id": "p2", "name": "B", "visits": []},
    ]
    result = compute_weekly_trend(patients, weeks=3, as_of=datetime(2026, 1, 19))
    assert [w.date() for w in result["weeks"]] == [datetime(2026, 1, 5).date(), datetime(2026, 1, 12).date(), datetime(2026, 1, 19).date()]
    row_a = next(r for r in result["patients"] if r["patient_id"] == "p1")
    assert row_a["scores"] == [80.0, 80.0, 50.0]
    row_b = next(r for r in result["patients"] if r["patient_id"] == "p2")
    assert row_b["scores"] == [None, None, None]


def test_weekly_trend_null_before_first_visit():
    patients = [{"patient_id": "p1", "name": "A", "visits": [{"date": "2026-01-20", "score": 30.0}]}]
    result = compute_weekly_trend(patients, weeks=3, as_of=datetime(2026, 1, 19))
    row = result["patients"][0]
    assert row["scores"] == [None, None, 30.0]


def test_diseases_overview_includes_diseases_with_zero_scored_patients():
    rows = [
        {"disease_id": "ADHD", "disease_name": "ADHD", "patient_id": "p1", "calculated_value": 80.0, "is_baseline": True},
        # zero-patient disease: LEFT JOIN placeholder, everything but disease_id/disease_name is NULL
        {"disease_id": "PTSD", "disease_name": "PTSD", "patient_id": None, "calculated_value": None, "is_baseline": None},
    ]
    result = compute_diseases_overview(rows, total_patients=1)
    by_id = {d["disease_id"]: d for d in result["diseases"]}
    assert by_id["PTSD"]["total"] == 0
    assert by_id["PTSD"]["improving"] == 0
    assert by_id["PTSD"]["insufficient_data"] == 0
    assert result["summary"]["disease_cohorts"] == 1  # PTSD has zero patients, excluded from the cohort count
    assert by_id["ADHD"]["total"] == 1


def test_group_protocol_captures_weights_scales_captured_in_same_session():
    weights = {"BDI-II": 60.0, "GAD-7": 40.0}
    rows = [
        {
            "patient_id": "p1",
            "first_name": "A",
            "last_name": "One",
            "recorded_at": "2026-01-05",
            "scale_code": "BDI-II",
            "percentage": 80.0,
        },
        {"patient_id": "p1", "first_name": "A", "last_name": "One", "recorded_at": "2026-01-05", "scale_code": "GAD-7", "percentage": 60.0},
        {
            "patient_id": "p1",
            "first_name": "A",
            "last_name": "One",
            "recorded_at": "2026-01-12",
            "scale_code": "BDI-II",
            "percentage": 40.0,
        },
        # patient with zero device-session captures still shows up, empty
        {"patient_id": "p2", "first_name": "B", "last_name": "Two", "recorded_at": None, "scale_code": None, "percentage": None},
    ]
    patients = group_protocol_captures_by_patient(rows, weights)
    p1 = next(p for p in patients if p["patient_id"] == "p1")
    assert [round(v["score"], 2) for v in p1["visits"]] == [72.0, 40.0]  # (80*60 + 60*40)/100, then single-scale session
    p2 = next(p for p in patients if p["patient_id"] == "p2")
    assert p2["visits"] == []


def test_group_protocol_captures_ignores_scale_not_in_disease_weights():
    weights = {"BDI-II": 100.0}
    rows = [
        {
            "patient_id": "p1",
            "first_name": "A",
            "last_name": "One",
            "recorded_at": "2026-01-05",
            "scale_code": "BDI-II",
            "percentage": 50.0,
        },
        {
            "patient_id": "p1",
            "first_name": "A",
            "last_name": "One",
            "recorded_at": "2026-01-05",
            "scale_code": "UNRELATED-SCALE",
            "percentage": 99.0,
        },
    ]
    patients = group_protocol_captures_by_patient(rows, weights)
    assert patients[0]["visits"] == [{"date": "2026-01-05", "score": 50.0}]


def _row(pid, label, code, name, pct, date):
    return {"patient_id": pid, "protocol_label": label, "scale_code": code, "scale_name": name, "percentage": pct, "recorded_at": date}


def test_protocol_outcomes_classifies_composite_trend_per_protocol():
    weights = {"BDI-II": 60.0, "GAD-7": 40.0}
    rows = [
        _row("p1", "tDCS — Left DLPFC", "BDI-II", "Beck Depression Inventory", 80.0, "2026-01-01"),
        _row("p1", "tDCS — Left DLPFC", "GAD-7", "GAD-7", 60.0, "2026-01-01"),
        _row("p1", "tDCS — Left DLPFC", "BDI-II", "Beck Depression Inventory", 40.0, "2026-02-01"),
        _row("p1", "tDCS — Left DLPFC", "GAD-7", "GAD-7", 30.0, "2026-02-01"),
        # single session -> insufficient_data
        _row("p2", "tDCS — Left DLPFC", "BDI-II", "Beck Depression Inventory", 50.0, "2026-01-01"),
    ]
    result = compute_protocol_outcomes(rows, weights)
    proto = result["protocols"][0]
    assert proto["protocol_label"] == "tDCS — Left DLPFC"
    assert proto["total"] == 2
    assert proto["improving"] == 1  # p1: (80*60+60*40)/100=72 -> (40*60+30*40)/100=36, >=20% drop
    assert proto["insufficient_data"] == 1


def test_protocol_outcomes_heatmap_averages_per_scale_change():
    weights = {"BDI-II": 100.0}
    rows = [
        _row("p1", "tDCS — Left DLPFC", "BDI-II", "Beck Depression Inventory", 80.0, "2026-01-01"),
        _row("p1", "tDCS — Left DLPFC", "BDI-II", "Beck Depression Inventory", 40.0, "2026-02-01"),
        _row("p2", "tDCS — Left DLPFC", "BDI-II", "Beck Depression Inventory", 60.0, "2026-01-01"),
        _row("p2", "tDCS — Left DLPFC", "BDI-II", "Beck Depression Inventory", 50.0, "2026-02-01"),
        # single point for this scale/protocol -> excluded from the heatmap
        _row("p3", "tDCS — Left DLPFC", "BDI-II", "Beck Depression Inventory", 20.0, "2026-01-01"),
    ]
    result = compute_protocol_outcomes(rows, weights)
    cell = result["heatmap"][0]
    assert cell["scale_code"] == "BDI-II"
    assert cell["n"] == 2
    assert cell["avg_change"] == -25.0  # ((40-80) + (50-60)) / 2


if __name__ == "__main__":
    test_responder_is_50pct_or_more_drop_from_baseline()
    test_less_than_50pct_drop_is_not_a_responder()
    test_single_visit_excluded_from_change_and_responder_math()
    test_patient_with_zero_visits_still_counted_in_patients_kpi()
    test_worsening_patient_gets_positive_change_and_no_responder_credit()
    test_zero_baseline_does_not_divide_by_zero()
    test_avg_assessments_averages_across_full_cohort_including_zero_visit_patients()
    test_trend_buckets_and_insufficient_data()
    test_provisional_pct_counts_patients_whose_latest_visit_is_provisional()
    test_diseases_overview_groups_and_classifies_per_disease()
    print("All reports service tests passed.")
