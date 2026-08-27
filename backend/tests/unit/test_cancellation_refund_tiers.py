"""resolve_cancellation_refund_percent is the one genuinely branchy piece of
the fee-breakdown/cancellation-tier redesign (64_fee_breakdown_and_
cancellation_policy.sql) — everything else is straight-line arithmetic or
plumbing. Tiers list mirrors what CancellationPolicyRepository.resolve_tiers
returns: sorted highest min_hours_before first."""

from app.modules.payments.service import _session_type_for, resolve_cancellation_refund_percent

TIERS = [
    {"min_hours_before": 12, "refund_percent": 100},
    {"min_hours_before": 6, "refund_percent": 50},
    {"min_hours_before": 2, "refund_percent": 20},
]


def test_free_cancellation_outside_the_top_tier():
    assert resolve_cancellation_refund_percent(TIERS, 24) == 100.0
    assert resolve_cancellation_refund_percent(TIERS, 12) == 100.0


def test_middle_tier_applies_between_thresholds():
    assert resolve_cancellation_refund_percent(TIERS, 8) == 50.0


def test_below_every_threshold_is_zero_not_the_smallest_tier():
    assert resolve_cancellation_refund_percent(TIERS, 1) == 0.0


def test_no_tiers_configured_is_zero():
    assert resolve_cancellation_refund_percent([], 24) == 0.0


def test_session_type_maps_device_session_and_everything_else_to_appointment():
    assert _session_type_for("device_session") == "device_session"
    assert _session_type_for("initial") == "appointment"
    assert _session_type_for("follow_up") == "appointment"
    assert _session_type_for("protocol_followup") == "appointment"
