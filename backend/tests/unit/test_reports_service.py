"""Doctor dashboard KPI math — pure aggregation, no database.

Business rules from Documents/Anava_Doctor_Portal_Analytics_Dashboard_v1.docx
Section 5.1 / DECISION table: baseline = first completed instance for the
disease, latest = most recent, responder = >=50% drop baseline->latest,
remitter = latest severity_level == "normal".
"""

from app.modules.reports.service import compute_dashboard_overview


def _visit(score, severity_level="moderate", date="2026-01-01"):
    return {"instance_id": "x", "date": date, "score": score, "severity_level": severity_level, "severity_label": severity_level.title()}


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


if __name__ == "__main__":
    test_responder_is_50pct_or_more_drop_from_baseline()
    test_less_than_50pct_drop_is_not_a_responder()
    test_single_visit_excluded_from_change_and_responder_math()
    test_patient_with_zero_visits_still_counted_in_patients_kpi()
    test_worsening_patient_gets_positive_change_and_no_responder_credit()
    test_zero_baseline_does_not_divide_by_zero()
    test_avg_assessments_averages_across_full_cohort_including_zero_visit_patients()
    print("All reports service tests passed.")
