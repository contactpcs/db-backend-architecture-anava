"""Scale-level scoring math — the 8 non-flat-sum scales plus the generic
flat-sum/direction/banding path. Pure functions, no database.

Worked values below are cross-checked against the examples in
Anava_PRS_Scoring_Engine_Specification_v1.docx Section 2/1.2 where the
doc gives one; synthetic otherwise.
"""

from app.modules.prs.scoring_rules import _psqi_parse_hhmm, compute_scale_score


def test_generic_flat_sum_normalizes_and_bands_higher_worse():
    # BDI-II worked example from the spec doc: 30/63 -> 47.6%
    result = compute_scale_score("BDI-II", items={}, naive_sum=30, naive_max=63)
    assert result["calculated_value"] == 30
    assert result["direction_corrected_percentage"] == 47.62
    assert result["severity_label"] == "Moderate Depression"


def test_generic_flat_sum_reverses_higher_better_direction():
    # Barthel: raw 80/100 is 20 points of dependency, i.e. 20% "bad" once reversed
    result = compute_scale_score("BARTHEL", items={}, naive_sum=80, naive_max=100)
    assert result["direction_corrected_percentage"] == 20.0
    assert result["risk_flags"] == ["functional_dependency"]


def test_unconfigured_scale_falls_back_to_higher_worse_generic_band():
    result = compute_scale_score("SOME-FUTURE-SCALE", items={}, naive_sum=50, naive_max=100)
    assert result["severity_label"] == "Moderate"
    assert result["severity_level"] == "moderate"


def test_compass31_applies_domain_multipliers_not_flat_sum():
    # 1 point on item 1 (orthostatic) should count x4, not x1 — this is the
    # exact bug the naive flat sum has today.
    items = {1: 1.0}
    result = compute_scale_score("COMPASS-31", items=items, naive_sum=1.0, naive_max=100.0)
    assert result["calculated_value"] == 4.0
    assert result["subscale_scores"]["orthostatic_intolerance"] == 4.0
    assert result["max_possible"] == 100.0


def test_dass21_applies_x2_multiplier_and_takes_worst_subscale():
    # depression items sum to 10 raw -> 20 after x2; anxiety/stress left at 0
    items = {3: 4, 5: 3, 10: 3}  # 3 of the 7 depression items
    result = compute_scale_score("DASS-21", items=items, naive_sum=10.0, naive_max=21.0)
    assert result["subscale_scores"]["depression"] == 20.0
    assert result["calculated_value"] == 20.0  # max of the 3 subscales
    assert result["max_possible"] == 42.0


def test_fiqr_applies_subscale_divisors():
    items = {i: 10 for i in range(1, 10)}  # function items maxed: 90/3=30
    items.update({10: 10, 11: 10})  # impact: 20/1=20
    items.update({i: 10 for i in range(12, 22)})  # symptoms: 100/2=50
    result = compute_scale_score("FIQR", items=items, naive_sum=0, naive_max=0)
    assert result["calculated_value"] == 100.0
    assert result["subscale_scores"] == {"function": 30.0, "overall_impact": 20.0, "symptoms": 50.0}


def test_paindetect_shift_formula():
    items = {i: 5 for i in range(1, 8)}  # sensory maxed at 35
    items[8] = 1  # course pattern
    items[9] = 1  # radiation
    result = compute_scale_score("PainDETECT", items=items, naive_sum=0, naive_max=0)
    assert result["calculated_value"] == 37  # 35+1+1
    assert result["direction_corrected_percentage"] == round((37 + 1) / 39 * 100, 2)


def test_ssqol_domain_average_and_reversal():
    items = {i: 5 for i in range(1, 50)}  # every item maxed at 5 (best possible)
    result = compute_scale_score("SS-QOL", items=items, naive_sum=0, naive_max=0)
    assert result["calculated_value"] == 5.0
    assert result["direction_corrected_percentage"] == 0.0  # best possible -> 0% severity


def test_snapiv_averages_three_subscale_percentages():
    items = {i: 3 for i in range(1, 10)}  # inattention maxed: 27/27=100%
    result = compute_scale_score("SNAP-IV", items=items, naive_sum=0, naive_max=0)
    assert result["subscale_scores"]["inattention"] == 27.0
    assert round(result["calculated_value"], 2) == round(100 / 3, 2)  # 1 of 3 subscales at 100%, others 0


def test_mfis_subscales_sum_to_total():
    items = {i: 2 for i in [4, 6, 7, 10, 13, 14, 17, 20, 21]}  # physical: 9 items x2 = 18
    result = compute_scale_score("MFIS", items=items, naive_sum=18, naive_max=84)
    assert result["subscale_scores"]["physical"] == 18.0
    assert result["calculated_value"] == 18.0


def test_sleep50_shift_formula_handles_min_of_50_not_0():
    items = {i: 1 for i in range(1, 51)}  # every item at its floor (1, not 0)
    result = compute_scale_score("SLEEP-50", items=items, naive_sum=0, naive_max=0)
    assert result["calculated_value"] == 50.0
    assert result["direction_corrected_percentage"] == 0.0  # floor score must read as 0% severity, not 25%


def test_fss_shift_formula_handles_min_of_9_not_0():
    # 9 items, each floor 1 (source PDF: "circle 1-7, 1=strongly disagree") ->
    # true floor is 9, not 0. Doc's own Section 2 table said "0-63" for a
    # "1-7 each" scale, which is the same self-contradiction PFS-16 had.
    result = compute_scale_score("FSS", items={}, naive_sum=9, naive_max=63)
    assert result["direction_corrected_percentage"] == 0.0  # floor score must read as 0%, not ~14%


def test_pfs16_shift_formula_handles_min_of_16_not_0():
    # 16 items, ordinal 1-5 each -> true floor is 16, not 0.
    result = compute_scale_score("PFS-16", items={}, naive_sum=16, naive_max=80)
    assert result["direction_corrected_percentage"] == 0.0  # floor score must read as 0%, not 20%


def test_eq5d5l_clinic_index_shift_formula():
    # All 5 dimensions at level 1 (best health) -> option points 4 each -> 20 raw
    result = compute_scale_score("EQ-5D-5L", items={}, naive_sum=20, naive_max=100)
    assert result["direction_corrected_percentage"] == 0.0  # perfect health must read as 0%, not 20%
    # All 5 dimensions at level 5 (worst health) -> option points 20 each -> 100 raw
    result = compute_scale_score("EQ-5D-5L", items={}, naive_sum=100, naive_max=100)
    assert result["direction_corrected_percentage"] == 100.0
    # 4 dimensions at level 5 (20 each) + 1 at level 4 (16) -> raw 96, matches
    # the user's own worked example ("96 to 100" range)
    result = compute_scale_score("EQ-5D-5L", items={}, naive_sum=96, naive_max=100)
    assert result["direction_corrected_percentage"] == 95.0


def test_asrs_weighted_score_all_maxed_equals_100():
    items = {1: 5, 2: 5, 3: 5, 4: 5, 5: 5, 6: 5}
    result = compute_scale_score("ASRS-v1.1", items=items, naive_sum=0, naive_max=0)
    assert result["calculated_value"] == 100.0


def test_asrs_weighted_score_all_below_threshold_equals_0():
    items = {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1}
    result = compute_scale_score("ASRS-v1.1", items=items, naive_sum=0, naive_max=0)
    assert result["calculated_value"] == 0.0


def test_asrs_weighted_score_worked_example():
    # Q1=Very Often, Q2=Often, Q3=Sometimes, Q4=Very Often, Q5=Often, Q6=Never
    items = {1: 5, 2: 4, 3: 3, 4: 5, 5: 4, 6: 1}
    result = compute_scale_score("ASRS-v1.1", items=items, naive_sum=0, naive_max=0)
    assert result["calculated_value"] == 58.33
    assert result["subscale_scores"]["q6"] == 0.0  # below Q6's own threshold


def test_asrs_part_b_answers_never_affect_the_score():
    # Part A all at Never/1 (score 0 on its own), Part B (items 7-18) maxed
    # at 5 — must not push the score up, since Part B is never scored.
    items = {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, **{i: 5 for i in range(7, 19)}}
    result = compute_scale_score("ASRS-v1.1", items=items, naive_sum=0, naive_max=0)
    assert result["calculated_value"] == 0.0


def test_psqi_full_worked_example_all_7_components():
    # Bed 23:00, wake 07:00 -> 8h in bed. Slept 7h -> efficiency 87.5% -> C4=0.
    # Q2=10min -> bucket 0; Q5a(item 5)=1 -> sum 1 -> C2=1.
    # Q4=7h -> C3=0 (>7 is 0, but 7 exactly falls to "6-7"->1; use 7.5 to be unambiguous)
    raw = {1: "23:00", 2: "10", 3: "07:00", 4: "7.5"}
    items = {
        5: 1,  # Q5a
        6: 0,
        7: 0,
        8: 0,
        9: 0,
        10: 0,
        11: 0,
        12: 0,
        13: 0,
        14: 0,  # Q5b-5j, sum=0 -> C5=0
        15: 1,  # Q6 medication -> C6=1 directly
        16: 1,
        17: 1,  # Q7+Q8=2 -> C7=1
        18: 1,  # Q9 -> C1=1
    }
    result = compute_scale_score("PSQI", items=items, naive_sum=0, naive_max=0, raw_responses=raw)
    # C1=1, C2=bucket(0+1)=1, C3=bucket(7.5)=0, C4=bucket(7.5/8*100=93.75%)=0, C5=0, C6=1, C7=bucket(2)=1
    assert result["subscale_scores"] == {
        "subjective_sleep_quality": 1,
        "sleep_latency": 1,
        "sleep_duration": 0,
        "sleep_efficiency": 0,
        "sleep_disturbance": 0,
        "medication_use": 1,
        "daytime_dysfunction": 1,
    }
    assert result["calculated_value"] == 4  # sum of all 7 components
    assert result["max_possible"] == 21  # all 7 components valid -> full 0-21 range
    assert result["severity_label"] == "Good Sleep Quality"


def test_psqi_unparseable_time_drops_only_that_component():
    raw = {1: "not a time", 2: "10", 3: "07:00", 4: "7.5"}
    items = {5: 0, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0, 11: 0, 12: 0, 13: 0, 14: 0, 15: 0, 16: 0, 17: 0, 18: 0}
    result = compute_scale_score("PSQI", items=items, naive_sum=0, naive_max=0, raw_responses=raw)
    assert "sleep_efficiency" not in result["subscale_scores"]  # bedtime unparseable -> C4 dropped
    assert result["max_possible"] == 18  # 6 valid components, not 7 -> 6*3, not silently scored out of 21


def test_psqi_accepts_12_hour_am_pm_format_defensively():
    # Same case as the overnight-wrap test, but in 12-hour display format —
    # the format actually shown by the frontend's time picker, unconfirmed
    # against what it submits, so both must parse identically.
    raw = {1: "11:30 PM", 2: "5", 3: "6:30 AM", 4: "7"}
    items = {5: 0, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0, 11: 0, 12: 0, 13: 0, 14: 0, 15: 0, 16: 0, 17: 0, 18: 0}
    result = compute_scale_score("PSQI", items=items, naive_sum=0, naive_max=0, raw_responses=raw)
    assert result["subscale_scores"]["sleep_efficiency"] == 0  # 7/7 = 100% efficiency -> C4=0
    # noon and midnight edge cases
    assert _psqi_parse_hhmm("12:00 AM") == 0
    assert _psqi_parse_hhmm("12:00 PM") == 12 * 60
    assert _psqi_parse_hhmm("11:59 PM") == 23 * 60 + 59


def test_psqi_overnight_wrap_efficiency():
    # Bed 23:30, wake 06:30 -> 7h in bed (crosses midnight)
    raw = {1: "23:30", 2: "5", 3: "06:30", 4: "7"}
    items = {5: 0, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0, 11: 0, 12: 0, 13: 0, 14: 0, 15: 0, 16: 0, 17: 0, 18: 0}
    result = compute_scale_score("PSQI", items=items, naive_sum=0, naive_max=0, raw_responses=raw)
    # 7 slept / 7 in bed = 100% efficiency -> C4=0 (>85%)
    assert result["subscale_scores"]["sleep_efficiency"] == 0


def test_mas_direct_100_scale_needs_no_special_scorer():
    # Option index 3 (of 0-5) is seeded with points=60 -> flat-sum path alone
    # gives calculated_value=60, max_possible=100 (from the seeded 0-100 option set).
    result = compute_scale_score("MAS", items={}, naive_sum=60, naive_max=100)
    assert result["calculated_value"] == 60
    assert result["direction_corrected_percentage"] == 60.0
    assert result["severity_label"] == "Moderate"  # generic tier, no native band


def test_moca_low_education_adjustment_capped_at_max():
    result = compute_scale_score("MoCA", items={}, naive_sum=30, naive_max=30, low_education=True)
    assert result["calculated_value"] == 30  # already at max, +1 must not overflow


if __name__ == "__main__":
    import sys

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failures else 0)
