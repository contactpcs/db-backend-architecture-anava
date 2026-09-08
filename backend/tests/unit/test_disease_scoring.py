"""Disease composite scoring — Sigma(scale % x weight%) / Sigma(weight%).

Pure functions, no database (mirrors test_scoring_rules.py). The full-weight-
set totals below were cross-checked against Documents/Anava_PRS_Scoring_
Engine_Specification_v1.docx Section 3's worked examples before being wired
into SQL/v1/81_disease_composite_weights.sql's seeded weight_pct values.
"""

from app.modules.prs.disease_scoring import compute_disease_composite

# Every one of the 14 diseases' full weight table (Section 3) and the
# illustrative direction-corrected scale percentages the spec doc's worked
# examples assume — same numbers used to hand-verify each disease's example
# total before this module existed.
DISEASE_WEIGHTS = {
    "Depression/Anxiety": {"BDI-II": 25, "GAD-7": 20, "DASS-21": 15, "MADRS": 15, "PSQI": 10, "COMPASS-31": 10, "EQ-5D-5L": 5},
    "Chronic Pain": {"PRS": 25, "DN-4": 15, "PainDETECT": 15, "DASS-21": 10, "GAD-7": 10, "PSQI": 10, "COMPASS-31": 10, "EQ-5D-5L": 5},
    "Fibromyalgia": {"FIQR": 40, "FSS": 15, "PRS": 15, "VAS": 10, "PainDETECT": 10, "COMPASS-31": 5, "EQ-5D-5L": 5},
    "Migraine": {"MIDAS": 30, "MSQ": 15, "PRS": 15, "DASS-21": 10, "BDI-II": 10, "PSQI": 10, "COMPASS-31": 5, "EQ-5D-5L": 5},
    "Ataxia": {"SARA": 40, "DHI": 15, "VVAS": 15, "DASS-21": 10, "BDI-II": 10, "COMPASS-31": 5, "EQ-5D-5L": 5},
    "After Stroke/TBI": {"BARTHEL": 25, "SS-QOL": 20, "KPS": 10, "MRC": 10, "MAS": 10, "MoCA": 10, "DASS-21": 5, "COMPASS-31": 5, "PainDETECT": 5},
    "Dementia": {"MoCA": 30, "AMTS": 20, "DSRS": 15, "GDS": 10, "IADL": 10, "DASS-21": 5, "COMPASS-31": 5, "EQ-5D-5L": 5},
    "Parkinson's Disease": {"PDSS": 30, "PFS-16": 25, "MoCA": 20, "PainDETECT": 15, "COMPASS-31": 10},
    "Tinnitus": {"THI": 50, "DASS-21": 15, "GAD-7": 10, "PSQI": 10, "COMPASS-31": 10, "EQ-5D-5L": 5},
    "Insomnia": {"PSQI": 25, "ISI": 20, "AIS": 15, "SLEEP-50": 10, "FFS": 10, "DASS-21": 5, "GAD-7": 5, "COMPASS-31": 5, "EQ-5D-5L": 5},
    "Multiple Sclerosis": {"MFIS": 30, "SARA": 20, "DHI": 15, "MoCA": 10, "BARTHEL": 10, "COMPASS-31": 10, "EQ-5D-5L": 5},
    "ADHD": {"ASRS-v1.1": 40, "SNAP-IV": 30, "DASS-21": 10, "COMPASS-31": 10, "EQ-5D-5L": 10},
    "ALS": {"ALSFRS-R": 40, "MAS": 15, "BDI-II": 10, "GAD-7": 10, "DASS-21": 10, "COMPASS-31": 10, "EQ-5D-5L": 5},
    "IBS (Irritable Bowel)": {"IBS-SSS": 40, "PRS": 15, "DASS-21": 10, "BDI-II": 10, "HDRS": 10, "COMPASS-31": 10, "EQ-5D-5L": 5},
}

EXAMPLE_PCTS = {
    "BDI-II": 60, "GAD-7": 55, "DASS-21": 65, "MADRS": 58, "PSQI": 45, "COMPASS-31": 40, "EQ-5D-5L": 50,
    "PRS": 70, "DN-4": 60, "PainDETECT": 50, "FIQR": 65, "FSS": 55, "VAS": 60, "MIDAS": 45, "MSQ": 50,
    "SARA": 30, "DHI": 40, "VVAS": 35, "BARTHEL": 25, "SS-QOL": 30, "KPS": 20, "MRC": 15, "MAS": 40,
    "MoCA": 35, "AMTS": 20, "DSRS": 30, "GDS": 25, "IADL": 20, "PDSS": 45, "PFS-16": 50, "THI": 60, "ISI": 55,
    "AIS": 50, "SLEEP-50": 40, "FFS": 45, "MFIS": 40, "ASRS-v1.1": 70, "SNAP-IV": 60, "ALSFRS-R": 50, "HDRS": 45,
    "IBS-SSS": 65,
}

# disease -> expected composite (rounded to 1dp), verified via
# compute_disease_examples.py before scoring_rules.py's composite code existed.
EXPECTED_TOTALS = {
    "Depression/Anxiety": 55.5,
    "Chronic Pain": 57.0,
    "Fibromyalgia": 60.2,
    "Migraine": 53.0,
    "Ataxia": 40.2,
    "After Stroke/TBI": 31.0,
    "Dementia": 31.2,
    "Parkinson's Disease": 44.5,
    "Tinnitus": 56.2,
    "Insomnia": 48.8,
    "Multiple Sclerosis": 36.5,
    "ADHD": 61.5,
    "ALS": 50.5,
    "IBS (Irritable Bowel)": 60.0,
}


def _inputs_for(disease: str) -> list[dict]:
    return [{"scale_code": code, "percentage": EXAMPLE_PCTS[code], "weight_pct": weight} for code, weight in DISEASE_WEIGHTS[disease].items()]


def test_all_14_diseases_match_spec_doc_worked_examples():
    for disease, expected in EXPECTED_TOTALS.items():
        result = compute_disease_composite(_inputs_for(disease))
        assert abs(result["composite_score"] - expected) < 0.15, f"{disease}: got {result['composite_score']}, expected {expected}"


def test_every_disease_weight_table_sums_to_100():
    for disease, weights in DISEASE_WEIGHTS.items():
        assert sum(weights.values()) == 100, disease


def test_full_completion_severity_bands_match_generic_tiers():
    # Depression/Anxiety's full-weight composite (55.5) falls in the 40-60
    # "Moderate" generic band used everywhere else in the app.
    result = compute_disease_composite(_inputs_for("Depression/Anxiety"))
    assert result["severity_level"] == "moderate"
    assert result["severity_label"] == "Moderate"
    assert set(result["scales_used"]) == set(DISEASE_WEIGHTS["Depression/Anxiety"])


def test_missing_scale_renormalizes_instead_of_zeroing_composite():
    # Only BDI-II (weight 25) and GAD-7 (weight 20) have been answered so
    # far for Depression/Anxiety — the other 5 assigned scales aren't done
    # yet. The composite must be computed over the 45 points that exist,
    # not diluted by the 55 missing points reading as 0.
    partial = [
        {"scale_code": "BDI-II", "percentage": 60, "weight_pct": 25},
        {"scale_code": "GAD-7", "percentage": 55, "weight_pct": 20},
    ]
    result = compute_disease_composite(partial)
    expected = (60 * 25 + 55 * 20) / 45
    assert abs(result["composite_score"] - expected) < 0.01
    assert result["scales_used"] == ["BDI-II", "GAD-7"]


def test_no_completed_scales_yields_no_composite():
    result = compute_disease_composite([])
    assert result["composite_score"] is None
    assert result["severity_level"] is None
    assert result["severity_label"] is None
    assert result["scales_used"] == []


def test_composite_is_clamped_to_0_100_range():
    # Defensive: even a pathological >100 or <0 input percentage can't push
    # the composite outside the valid band.
    result = compute_disease_composite([{"scale_code": "X", "percentage": 500, "weight_pct": 100}])
    assert result["composite_score"] == 100.0

    result = compute_disease_composite([{"scale_code": "X", "percentage": -50, "weight_pct": 100}])
    assert result["composite_score"] == 0.0


if __name__ == "__main__":
    test_all_14_diseases_match_spec_doc_worked_examples()
    test_every_disease_weight_table_sums_to_100()
    test_full_completion_severity_bands_match_generic_tiers()
    test_missing_scale_renormalizes_instead_of_zeroing_composite()
    test_no_completed_scales_yields_no_composite()
    test_composite_is_clamped_to_0_100_range()
    print("All disease composite tests passed.")
