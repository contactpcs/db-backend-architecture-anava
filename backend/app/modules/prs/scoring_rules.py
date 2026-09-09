"""Scale-level scoring rules for the 37 resolved PRS scales.

Pure functions only — no DB access. Source of truth is Documents/
Anava_PRS_Scoring_Engine_Specification_v1.docx (Section 2). Called from
prs/service.py::_finalize_scale with the per-item response values already
loaded from the DB.

29 of the 37 scales are a flat sum of item points — the existing
sum_for_scale()/max_points_for_question() computation in service.py is
already correct for those and is passed through unchanged (see
SPECIAL_SCORERS below for the 8 that are not a flat sum: COMPASS-31's
domain multipliers, and the subscale-split scales DASS-21/FIQR/MSQ/
SNAP-IV/MFIS/SS-QOL/PainDETECT).

PSQI, EQ-5D-5L and ASRS-v1.1 have since been resolved by clinic decision
(see their SPECIAL_SCORERS entries below). Only MAS remains unresolved —
it is a clinician physical exam, not a patient questionnaire, and needs a
different capture workflow entirely (see Section 4 of the spec doc).
"""
from __future__ import annotations

import re
from collections.abc import Callable

HIGHER_WORSE = "higher_worse"
HIGHER_BETTER = "higher_better"

# direction + native severity bands, evaluated against the RAW calculated_value
# (never the normalized/reversed percentage). bands: list of (upper_bound_inclusive,
# label) in ascending order; upper_bound=None means "open-ended, last tier".
# bands=None means this scale has no published 5-tier band — falls back to the
# generic 0–100 severity table (Section 1.5 of the spec doc).
SCALE_CONFIG: dict[str, dict] = {
    "AIS": {"direction": HIGHER_WORSE, "bands": None},
    "ALSFRS-R": {"direction": HIGHER_BETTER, "bands": None},
    "AMTS": {"direction": HIGHER_BETTER, "bands": [(3, "Severe Impairment"), (6, "Moderate Impairment"), (None, "Normal")]},
    # Clinic-defined weighted index — Part A only (6 items). Part B (12
    # items) is captured as normal patient responses but never enters this
    # scale's math, per the source instrument: "No total score ... is
    # utilized for the twelve questions." Not the original published binary
    # screen (superseded by clinic decision) — see _score_asrs below.
    "ASRS-v1.1": {"direction": HIGHER_WORSE, "bands": None},
    "BARTHEL": {"direction": HIGHER_BETTER, "bands": [(20, "Total Dependency"), (60, "Severe Dependency"), (90, "Moderate Dependency"), (99, "Slight Dependency"), (None, "Independent")]},
    "BDI-II": {"direction": HIGHER_WORSE, "bands": [(10, "Normal"), (16, "Mild Mood Disturbance"), (20, "Borderline Clinical Depression"), (30, "Moderate Depression"), (40, "Severe Depression"), (None, "Extreme Depression")]},
    "COMPASS-31": {"direction": HIGHER_WORSE, "bands": [(23, "Remission/Low"), (40, "Mild"), (63, "Moderate"), (82, "Severe"), (None, "Very Severe")]},
    "DASS-21": {"direction": HIGHER_WORSE, "bands": None},
    "DHI": {"direction": HIGHER_WORSE, "bands": [(15, "No/Slight Handicap"), (34, "Mild Handicap"), (52, "Moderate Handicap"), (None, "Severe Handicap")]},
    "DN-4": {"direction": HIGHER_WORSE, "bands": None},
    "DSRS": {"direction": HIGHER_WORSE, "bands": [(18, "Mild"), (36, "Moderate"), (None, "Severe")]},
    # Clinic-defined index, not the licensed official EQ-5D-5L value set (see
    # Section 4 of the spec doc). 5 dimensions x 5 levels, each option seeded
    # as level x 4 points (4/8/12/16/20) per clinic decision, so raw sum
    # ranges 20-100 -> shifted to true 0-100 by the normalizer below.
    "EQ-5D-5L": {"direction": HIGHER_WORSE, "bands": None},
    "FFS": {"direction": HIGHER_WORSE, "bands": [(12, "Normal"), (15, "Borderline"), (20, "Moderate"), (None, "Severe")]},
    "FIQR": {"direction": HIGHER_WORSE, "bands": None},
    "FSS": {"direction": HIGHER_WORSE, "bands": None},
    "GAD-7": {"direction": HIGHER_WORSE, "bands": [(4, "Minimal"), (9, "Mild"), (14, "Moderate"), (None, "Severe")]},
    "GDS": {"direction": HIGHER_WORSE, "bands": None},
    "HDRS": {"direction": HIGHER_WORSE, "bands": [(7, "Normal/Remission"), (13, "Mild"), (18, "Moderate"), (22, "Severe"), (None, "Very Severe")]},
    "IADL": {"direction": HIGHER_BETTER, "bands": None},
    "IBS-SSS": {"direction": HIGHER_WORSE, "bands": [(75, "Normal/Remission"), (175, "Mild"), (300, "Moderate"), (None, "Severe")]},
    "ISI": {"direction": HIGHER_WORSE, "bands": [(7, "Not Clinically Significant"), (14, "Subthreshold"), (21, "Clinical - Moderate"), (None, "Clinical - Severe")]},
    "KPS": {"direction": HIGHER_BETTER, "bands": None},
    "MADRS": {"direction": HIGHER_WORSE, "bands": [(6, "Normal"), (19, "Mild"), (30, "Moderate"), (39, "Severe"), (None, "Extremely Severe")]},
    # Clinic-defined direct scoring: the single question's 6 options (raw
    # Ashworth grades 0-5) are seeded with points 0/20/40/60/80/100, so the
    # existing flat-sum path already produces the final 0-100 score with no
    # special scorer needed. Still captures the clinician's own pre-averaged
    # judgment across muscles tested, not per-muscle data (see Section 4 of
    # the spec doc) — a data-capture limitation, not a scoring-formula one.
    "MAS": {"direction": HIGHER_WORSE, "bands": None},
    "MFIS": {"direction": HIGHER_WORSE, "bands": None},
    "MIDAS": {"direction": HIGHER_WORSE, "bands": [(5, "Little/No Disability"), (10, "Mild Disability"), (20, "Moderate Disability"), (None, "Severe Disability")]},
    "MoCA": {"direction": HIGHER_BETTER, "bands": [(25, "Below Normal"), (None, "Normal")]},
    "MRC": {"direction": HIGHER_BETTER, "bands": None},
    "MSQ": {"direction": HIGHER_BETTER, "bands": None},
    "PainDETECT": {"direction": HIGHER_WORSE, "bands": None},
    "PDSS": {"direction": HIGHER_WORSE, "bands": None},
    "PFS-16": {"direction": HIGHER_WORSE, "bands": None},
    "PRS": {"direction": HIGHER_WORSE, "bands": None},
    # 7 components (0-3 each) summed, range 0-21. Native band uses the
    # developers' own validated cutoff (score >5 discriminates poor from
    # good sleepers at 89.6% sensitivity / 86.5% specificity).
    "PSQI": {"direction": HIGHER_WORSE, "bands": [(5, "Good Sleep Quality"), (None, "Poor Sleep Quality")]},
    "SARA": {"direction": HIGHER_WORSE, "bands": [(5, "No/Minimal Ataxia"), (None, "Ataxia Present")]},
    "SLEEP-50": {"direction": HIGHER_WORSE, "bands": None},
    "SNAP-IV": {"direction": HIGHER_WORSE, "bands": None},
    "SS-QOL": {"direction": HIGHER_BETTER, "bands": None},
    "THI": {"direction": HIGHER_WORSE, "bands": [(16, "Slight/No Handicap"), (36, "Mild Handicap"), (56, "Moderate Handicap"), (76, "Severe Handicap"), (None, "Catastrophic Handicap")]},
    "VAS": {"direction": HIGHER_WORSE, "bands": None},
    "VVAS": {"direction": HIGHER_WORSE, "bands": [(40, "Mild"), (70, "Moderate"), (None, "Severe")]},
}

# Single-scale clinical thresholds (from the disease "Diagnosis" rules in the
# spec doc) — evaluated against the raw calculated_value, independent of
# severity banding. Only the thresholds that are genuinely single-scale;
# cross-scale disease diagnosis rules (e.g. "BDI>=21 OR GAD>=10") live at the
# composite layer, not here.
_RISK_THRESHOLDS: dict[str, tuple[float, str, str]] = {
    "DN-4": (4, ">=", "neuropathic_pain_likely"),
    "MoCA": (26, "<", "cognitive_impairment_likely"),
    "AMTS": (6, "<=", "cognitive_impairment_likely"),
    "IBS-SSS": (175, ">=", "moderate_or_severe_ibs"),
    "MIDAS": (11, ">=", "severe_migraine_disability"),
    "THI": (18, ">=", "clinically_significant_tinnitus_handicap"),
    "GAD-7": (10, ">=", "moderate_or_severe_anxiety"),
    "BDI-II": (21, ">=", "moderate_or_severe_depression"),
    "FIQR": (50, ">=", "significant_fibromyalgia_impact"),
    "ISI": (15, ">=", "clinical_insomnia"),
    "SARA": (5, ">", "ataxia_present"),
    "BARTHEL": (90, "<", "functional_dependency"),
    "PSQI": (5, ">", "poor_sleep_quality"),
}

_GENERIC_TIERS = [(20, "normal", "Normal"), (40, "mild", "Mild"), (60, "moderate", "Moderate"), (80, "severe", "Severe"), (101, "very_severe", "Very Severe")]


def _lookup_band(bands, value: float) -> str:
    for upper, label in bands:
        if upper is None or value <= upper:
            return label
    return bands[-1][1]


def _generic_tier(pct: float) -> tuple[str, str]:
    for upper, level, label in _GENERIC_TIERS:
        if pct <= upper:
            return level, label
    return "very_severe", "Very Severe"


def _risk_flags_for(scale_code: str, calculated_value: float) -> list[str] | None:
    # A list, not {name: hit} — prs_scale_results.risk_flags is JSONB NOT NULL
    # DEFAULT '[]' and the recalculate_final_result trigger (SQL/07_prs_tables.
    # sql) does jsonb_array_length(r.risk_flags) / v_all_flags || r.risk_flags
    # to fold every scale's flags into prs_final_results.all_risk_flags — an
    # object there throws "cannot get array length of a non-array".
    rule = _RISK_THRESHOLDS.get(scale_code)
    if not rule:
        return None
    threshold, op, name = rule
    hit = {
        ">=": calculated_value >= threshold,
        "<": calculated_value < threshold,
        "<=": calculated_value <= threshold,
        ">": calculated_value > threshold,
    }[op]
    return [name] if hit else None


# ---------------------------------------------------------------------------
# Special scorers — the 8 scales that are not a flat sum of item points.
# Each takes items: dict[int, float] keyed by 1-based item/question position
# (prs_scale_question_map.display_order) -> the response's point value, and
# returns (calculated_value, max_possible, subscale_scores | None).
# ---------------------------------------------------------------------------

# Clinic-defined weighted ASRS-v1.1 Part A scoring (replaces the original
# published binary "4-of-6 dark boxes" screen by clinic decision, so the
# dashboard gets a continuous 0-100 trend instead of a 0/100 flip). Options
# are seeded 1-5 (1=Never, 2=Rarely, 3=Sometimes, 4=Often, 5=Very Often).
# Each of the 6 questions carries an equal 100/6 budget; within a question,
# weight increases with severity; any option below the question's own
# "dark shaded" floor is worth 0. Maxing every question sums to exactly 100.
_ASRS_WEIGHTS: dict[int, dict[int, float]] = {
    1: {3: 5.556, 4: 11.111, 5: 16.667},
    2: {3: 5.556, 4: 11.111, 5: 16.667},
    3: {3: 5.556, 4: 11.111, 5: 16.667},
    4: {4: 8.333, 5: 16.667},
    5: {4: 8.333, 5: 16.667},
    6: {4: 8.333, 5: 16.667},
}


def _score_asrs(items: dict[int, float]):
    subscales = {}
    total = 0.0
    for q, weights in _ASRS_WEIGHTS.items():
        answer = int(items.get(q, 0))
        weight = weights.get(answer, 0.0)
        subscales[f"q{q}"] = weight
        total += weight
    return round(total, 2), 100.0, subscales


def _score_compass31(items: dict[int, float]):
    def s(a, b):
        return sum(items.get(i, 0.0) for i in range(a, b + 1))

    orthostatic = s(1, 4) * 4
    vasomotor = s(5, 7) * 0.8333
    secretomotor = s(8, 11) * 2.1428571
    gastrointestinal = s(12, 23) * 0.8928571
    bladder = s(24, 26) * 1.111
    pupillomotor = s(27, 31) * 0.333
    total = orthostatic + vasomotor + secretomotor + gastrointestinal + bladder + pupillomotor
    subscales = {
        "orthostatic_intolerance": round(orthostatic, 2),
        "vasomotor": round(vasomotor, 2),
        "secretomotor": round(secretomotor, 2),
        "gastrointestinal": round(gastrointestinal, 2),
        "bladder": round(bladder, 2),
        "pupillomotor": round(pupillomotor, 2),
    }
    return total, 100.0, subscales


_DASS_GROUPS = {"depression": [3, 5, 10, 13, 16, 17, 21], "anxiety": [2, 4, 7, 9, 15, 19, 20], "stress": [1, 6, 8, 11, 12, 14, 18]}


def _score_dass21(items: dict[int, float]):
    # Official DASS-21 scoring: each 7-item subscale raw sum x2. The
    # composite value fed to disease formulas is the worst (highest) of the
    # 3 subscales — the spec doc's "(score/21)*100 (max of depression/anxiety)"
    # rule, generalized to all three.
    subscales = {name: sum(items.get(i, 0.0) for i in idxs) * 2 for name, idxs in _DASS_GROUPS.items()}
    calculated_value = max(subscales.values()) if subscales else 0.0
    return calculated_value, 42.0, {k: round(v, 2) for k, v in subscales.items()}


def _score_fiqr(items: dict[int, float]):
    function = sum(items.get(i, 0.0) for i in range(1, 10)) / 3
    overall_impact = sum(items.get(i, 0.0) for i in range(10, 12)) / 1
    symptoms = sum(items.get(i, 0.0) for i in range(12, 22)) / 2
    total = function + overall_impact + symptoms
    subscales = {"function": round(function, 2), "overall_impact": round(overall_impact, 2), "symptoms": round(symptoms, 2)}
    return total, 100.0, subscales


def _score_msq(items: dict[int, float]):
    restrictive = (sum(items.get(i, 0.0) for i in range(1, 8)) - 7) * 100 / 35
    preventive = (sum(items.get(i, 0.0) for i in range(8, 12)) - 4) * 100 / 20
    emotional = (sum(items.get(i, 0.0) for i in range(12, 15)) - 3) * 100 / 15
    avg = (restrictive + preventive + emotional) / 3
    subscales = {
        "role_function_restrictive": round(restrictive, 2),
        "role_function_preventive": round(preventive, 2),
        "emotional_function": round(emotional, 2),
    }
    return avg, 100.0, subscales


def _score_paindetect(items: dict[int, float]):
    # Items 1-7: sensory descriptors, already weighted 0-5 each (x0..x5 per
    # the printed scoring key) -> 0-35. Item 8: pain-course-pattern, seeded
    # as -1/0/+1/+1 per the 4 categorical options. Item 9: radiation, 0 or 1.
    sensory = sum(items.get(i, 0.0) for i in range(1, 8))
    course_pattern = items.get(8, 0.0)
    radiation = items.get(9, 0.0)
    raw = sensory + course_pattern + radiation
    subscales = {"sensory": round(sensory, 2), "course_pattern": course_pattern, "radiation": radiation}
    return raw, 38.0, subscales


_SNAP_GROUPS = {"inattention": (range(1, 10), 27), "hyperactivity_impulsivity": (range(10, 19), 27), "oppositional_defiant": (range(19, 27), 24)}


def _score_snapiv(items: dict[int, float]):
    subscales_raw = {name: sum(items.get(i, 0.0) for i in idxs) for name, (idxs, _max) in _SNAP_GROUPS.items()}
    pct = sum((subscales_raw[name] / _max) * 100 for name, (_idxs, _max) in _SNAP_GROUPS.items()) / len(_SNAP_GROUPS)
    return pct, 100.0, {k: round(v, 2) for k, v in subscales_raw.items()}


_MFIS_GROUPS = {"physical": [4, 6, 7, 10, 13, 14, 17, 20, 21], "cognitive": [1, 2, 3, 5, 11, 12, 15, 16, 18, 19], "psychosocial": [8, 9]}


def _score_mfis(items: dict[int, float]):
    subscales = {name: sum(items.get(i, 0.0) for i in idxs) for name, idxs in _MFIS_GROUPS.items()}
    total = sum(subscales.values())
    return total, 84.0, {k: round(v, 2) for k, v in subscales.items()}


# Item order matches the printed instrument sequence (Energy, Family Roles,
# Language, Mobility, Mood, Personality, Self Care, Social Roles, Thinking,
# Upper Extremity, Vision, Work/Productivity) — confirm against the source
# SS-QOL PDF before relying on this for a real patient if the seeded question
# order ever changes.
_SSQOL_DOMAINS = {
    "energy": range(1, 4), "family_roles": range(4, 7), "language": range(7, 12), "mobility": range(12, 18),
    "mood": range(18, 23), "personality": range(23, 26), "self_care": range(26, 31), "social_roles": range(31, 36),
    "thinking": range(36, 39), "upper_extremity": range(39, 44), "vision": range(44, 47), "work_productivity": range(47, 50),
}


def _score_ssqol(items: dict[int, float]):
    domain_avgs = {}
    for name, idxs in _SSQOL_DOMAINS.items():
        vals = [items.get(i, 0.0) for i in idxs]
        domain_avgs[name] = sum(vals) / len(vals) if vals else 0.0
    overall_avg = sum(domain_avgs.values()) / len(domain_avgs)
    return overall_avg, 5.0, {k: round(v, 2) for k, v in domain_avgs.items()}


# Item order matches the printed instrument's 9 sections (sleep apnea,
# insomnia, narcolepsy, RLS/PLMD, circadian rhythm, sleepwalking,
# nightmares, factors influencing sleep, impact) — 8+8+5+4+3+3+5+7+7=50
# items, each scored 1 ("not at all") to 4 ("very much"), so the scale's
# true range is 50-200, not 0-200 (see the SLEEP-50 normalizer below).
_SLEEP50_GROUPS = {
    "sleep_apnea": range(1, 9), "insomnia": range(9, 17), "narcolepsy": range(17, 22),
    "rls_plmd": range(22, 26), "circadian_rhythm": range(26, 29), "sleepwalking": range(29, 32),
    "nightmares": range(32, 37), "factors_influencing_sleep": range(37, 44), "impact": range(44, 51),
}


def _score_sleep50(items: dict[int, float]):
    subscales = {name: sum(items.get(i, 0.0) for i in idxs) for name, idxs in _SLEEP50_GROUPS.items()}
    total = sum(subscales.values())
    return total, 200.0, {k: round(v, 2) for k, v in subscales.items()}


# ---------------------------------------------------------------------------
# PSQI — the one scale that needs raw typed answers (clock times, minutes,
# hours), not just option points, per the official Buysse et al. scoring
# guide (TR4872appendixS1.pdf). Item numbers below are display_order in our
# seed data: 1=bedtime, 2=latency minutes, 3=wake time, 4=hours slept,
# 5-14=Q5a-Q5j (points 0-3 each), 15=Q6 medication, 16=Q7 daytime sleepiness,
# 17=Q8 enthusiasm problem, 18=Q9 subjective quality. Q10 (bed partner) is
# excluded — the source guide states it does not contribute to the score.
# Any component that can't be computed (e.g. an unparseable bedtime) is
# dropped from both the sum and max_possible, the same re-normalize-on-
# missing-data approach used at the disease-composite layer.
# ---------------------------------------------------------------------------

def _psqi_parse_hhmm(text: str | None):
    """Returns minutes-since-midnight, or None if unparseable. Accepts both
    24-hour "23:11" (what a native <input type="time"> normally submits)
    and 12-hour "11:11 PM" (defensive — the frontend's time picker displays
    12-hour, and its submitted value format was not confirmed at the time
    this was written)."""
    if not text:
        return None
    text = text.strip()

    m = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    m = re.match(r"^(\d{1,2}):(\d{2})\s*([AaPp][Mm])$", text)
    if m:
        hour, minute, meridiem = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        if hour < 1 or hour > 12 or minute > 59:
            return None
        if meridiem == "AM":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
        return hour * 60 + minute

    return None


def _psqi_to_float(text: str | None):
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _psqi_bucket_minutes(minutes: float) -> int:
    if minutes <= 15:
        return 0
    if minutes <= 30:
        return 1
    if minutes <= 60:
        return 2
    return 3


def _psqi_bucket_duration_hours(hours: float) -> int:
    if hours > 7:
        return 0
    if hours > 6:
        return 1
    if hours > 5:
        return 2
    return 3


def _psqi_bucket_sum6(total: float) -> int:
    # component 2 (Q2+Q5a, max 6) and component 7 (Q7+Q8, max 6) share this table
    if total <= 0:
        return 0
    if total <= 2:
        return 1
    if total <= 4:
        return 2
    return 3


def _psqi_bucket_disturbance(total: float) -> int:
    if total <= 0:
        return 0
    if total <= 9:
        return 1
    if total <= 18:
        return 2
    return 3


def _psqi_bucket_efficiency(pct: float) -> int:
    if pct > 85:
        return 0
    if pct >= 75:
        return 1
    if pct >= 65:
        return 2
    return 3


def _psqi_sleep_efficiency_pct(bed_text, wake_text, hours_slept):
    bed = _psqi_parse_hhmm(bed_text)
    wake = _psqi_parse_hhmm(wake_text)
    if bed is None or wake is None or hours_slept is None:
        return None
    hours_in_bed = ((wake - bed) % 1440) / 60.0
    if hours_in_bed <= 0:
        return None
    return (hours_slept / hours_in_bed) * 100.0


def _score_psqi(items: dict[int, float], raw: dict[int, str]):
    minutes = _psqi_to_float(raw.get(2))
    hours_slept = _psqi_to_float(raw.get(4))

    c1 = items.get(18)  # Q9, already native 0-3

    c2 = None
    if minutes is not None and 5 in items:
        c2 = _psqi_bucket_sum6(_psqi_bucket_minutes(minutes) + items[5])

    c3 = _psqi_bucket_duration_hours(hours_slept) if hours_slept is not None else None

    efficiency_pct = _psqi_sleep_efficiency_pct(raw.get(1), raw.get(3), hours_slept)
    c4 = _psqi_bucket_efficiency(efficiency_pct) if efficiency_pct is not None else None

    disturbance_items = [items[i] for i in range(6, 15) if i in items]
    c5 = _psqi_bucket_disturbance(sum(disturbance_items)) if len(disturbance_items) == 9 else None

    c6 = items.get(15)  # Q6, already native 0-3

    c7 = _psqi_bucket_sum6(items[16] + items[17]) if 16 in items and 17 in items else None

    components = {
        "subjective_sleep_quality": c1,
        "sleep_latency": c2,
        "sleep_duration": c3,
        "sleep_efficiency": c4,
        "sleep_disturbance": c5,
        "medication_use": c6,
        "daytime_dysfunction": c7,
    }
    valid = {k: v for k, v in components.items() if v is not None}
    calculated_value = sum(valid.values())
    max_possible = len(valid) * 3
    return calculated_value, max_possible, valid


SPECIAL_SCORERS: dict[str, Callable[..., tuple]] = {
    "ASRS-v1.1": _score_asrs,
    "COMPASS-31": _score_compass31,
    "DASS-21": _score_dass21,
    "FIQR": _score_fiqr,
    "MSQ": _score_msq,
    "PainDETECT": _score_paindetect,
    "SNAP-IV": _score_snapiv,
    "MFIS": _score_mfis,
    "SS-QOL": _score_ssqol,
    "SLEEP-50": _score_sleep50,
    "PSQI": _score_psqi,
}

# Scales whose percentage isn't a plain calculated_value/max_possible ratio
# (all three are shift-then-divide formulas from the spec doc — each item's
# true minimum is above 0, so a plain value/max ratio would understate severity).
_NORMALIZERS = {
    "PainDETECT": lambda v, m: ((v + 1) / 39) * 100,
    "SS-QOL": lambda v, m: ((v - 1) / 4) * 100,
    "SLEEP-50": lambda v, m: ((v - 50) / 150) * 100,
    # FSS: 9 items scored 1 ("strongly disagree") to 7 ("strongly agree") —
    # true range is 9-63, not 0-63 (confirmed against the source FSS PDF).
    "FSS": lambda v, m: ((v - 9) / 54) * 100,
    # PFS-16: 16 items scored 1 ("strongly disagree") to 5 ("strongly
    # agree") on the ordinal method — true range is 16-80, not 0-80.
    "PFS-16": lambda v, m: ((v - 16) / 64) * 100,
    # EQ-5D-5L (clinic-defined index): 5 questions x options worth
    # 4/8/12/16/20 -> raw sum floor is 20 (all level 1), not 0.
    "EQ-5D-5L": lambda v, m: ((v - 20) / 80) * 100,
}


def compute_scale_score(
    scale_code: str,
    items: dict[int, float],
    *,
    naive_sum: float,
    naive_max: float,
    low_education: bool = False,
    raw_responses: dict[int, str] | None = None,
) -> dict:
    """Scores one scale for one PRS instance.

    scale_code: e.g. "GAD-7" (prs_scales.scale_code, not scale_id).
    items: {display_order (1-based) -> response_value} for every answered,
        non-skipped question in the scale — as loaded by service.py.
    naive_sum/naive_max: the existing flat-sum computation from
        PrsResponseRepository.sum_for_scale()/max_points_for_question() —
        already correct for the scales with no entry in SPECIAL_SCORERS.
    low_education: True if the patient has <=12 years of formal education —
        only affects MoCA (+1 point, capped at max). Wire this to wherever
        education level is captured; defaults to False until that's found.
    raw_responses: {display_order -> given_response text}, only needed by
        PSQI (clock times and raw minutes/hours aren't option-point-based).
        Every other scale ignores this.
    """
    scorer = SPECIAL_SCORERS.get(scale_code)
    subscale_scores = None
    if scorer:
        if scale_code == "PSQI":
            calculated_value, max_possible, subscale_scores = scorer(items, raw_responses or {})
        else:
            calculated_value, max_possible, subscale_scores = scorer(items)
    else:
        calculated_value, max_possible = naive_sum, naive_max
        if scale_code == "MoCA" and low_education and max_possible > 0:
            calculated_value = min(calculated_value + 1, max_possible)

    normalizer = _NORMALIZERS.get(scale_code)
    if normalizer:
        pct_raw = normalizer(calculated_value, max_possible)
    elif max_possible > 0:
        pct_raw = (calculated_value / max_possible) * 100
    else:
        pct_raw = 0.0
    pct_raw = max(0.0, min(100.0, pct_raw))

    cfg = SCALE_CONFIG.get(scale_code, {"direction": HIGHER_WORSE, "bands": None})
    direction_corrected_pct = (100 - pct_raw) if cfg["direction"] == HIGHER_BETTER else pct_raw

    level, generic_label = _generic_tier(direction_corrected_pct)
    native_label = _lookup_band(cfg["bands"], calculated_value) if cfg.get("bands") else None

    return {
        "calculated_value": round(calculated_value, 2),
        "max_possible": round(max_possible, 2),
        "severity_level": level,
        "severity_label": native_label or generic_label,
        "subscale_scores": subscale_scores,
        "risk_flags": _risk_flags_for(scale_code, calculated_value),
        "direction_corrected_percentage": round(direction_corrected_pct, 2),
    }
