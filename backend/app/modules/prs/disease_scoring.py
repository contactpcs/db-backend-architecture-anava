"""Disease-level composite scoring — Σ(scale % × weight%) / Σ(weight%).

Pure functions only — no DB access, same convention as scoring_rules.py.
Source of truth is Documents/Anava_PRS_Scoring_Engine_Specification_v1.docx
(Section 3) — the weight tables live in the DB (reference.prs_disease_scale_map.
weight_pct, seeded by SQL/v1/81_disease_composite_weights.sql) rather than
being duplicated here, so there is exactly one place that can drift from the
spec doc.

Each scale's input percentage must already be its DIRECTION-CORRECTED 0-100
value (scoring_rules.compute_scale_score()'s "direction_corrected_percentage"
— never the raw calculated_value/max_possible ratio), since "higher = worse"
is the universal convention the composite formula assumes.
"""
from __future__ import annotations

# Same 5-tier generic banding as scoring_rules._GENERIC_TIERS — duplicated
# rather than imported to keep this module's only dependency being its own
# input list, matching scoring_rules.py's zero-cross-module-imports style.
_GENERIC_TIERS = [(20, "normal", "Normal"), (40, "mild", "Mild"), (60, "moderate", "Moderate"), (80, "severe", "Severe"), (101, "very_severe", "Very Severe")]


def _generic_tier(pct: float) -> tuple[str, str]:
    for upper, level, label in _GENERIC_TIERS:
        if pct <= upper:
            return level, label
    return "very_severe", "Very Severe"


def compute_disease_composite(scale_inputs: list[dict]) -> dict:
    """scale_inputs: one dict per scale that has BOTH a completed scale result
    AND a weight_pct row for this disease —
    [{"scale_code": str, "percentage": float, "weight_pct": float}, ...].

    A scale the patient hasn't answered yet is simply absent from this list.
    Weights are renormalized to sum to 100 over whatever IS present, so an
    incomplete assessment reads as a real (if provisional) composite instead
    of being pulled toward 0 by missing scales counting as zero. This is a
    data-availability accommodation, not a scoring-formula gap — once every
    weighted scale is present, this is exactly the doc's Σ(scale%×weight%)
    formula (weights already sum to 100 in the DB, so renormalizing is a
    no-op at full completion). Same renormalize-on-missing-data approach
    already used inside PSQI's own component scoring (scoring_rules.py::
    _score_psqi).
    """
    total_weight = sum(s["weight_pct"] for s in scale_inputs)
    if total_weight <= 0:
        return {"composite_score": None, "severity_level": None, "severity_label": None, "scales_used": []}

    composite = sum(s["percentage"] * s["weight_pct"] for s in scale_inputs) / total_weight
    composite = max(0.0, min(100.0, composite))
    level, label = _generic_tier(composite)
    return {
        "composite_score": round(composite, 2),
        "severity_level": level,
        "severity_label": label,
        "scales_used": [s["scale_code"] for s in scale_inputs],
    }
