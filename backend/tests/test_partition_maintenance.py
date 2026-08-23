"""Partition naming must round-trip: names produced by _next_range have to parse
back through _partition_upper_bound, or the retention job silently stops being
able to drop the partitions this worker creates. No DB needed — pure date logic.
"""

from datetime import UTC, datetime

from app.workers.retention_purge import _next_range, _partition_upper_bound


def test_monthly_roundtrip_across_year_boundary():
    lower = datetime(2027, 12, 1, tzinfo=UTC)
    for _ in range(15):  # walks 2027-12 -> 2029-02, crossing two year rollovers
        upper, suffix = _next_range(lower, monthly=True)
        assert _partition_upper_bound(f"audit_logs_{suffix}", "audit_logs") == upper
        assert upper > lower
        lower = upper


def test_yearly_roundtrip():
    lower = datetime(2027, 1, 1, tzinfo=UTC)
    for _ in range(4):
        upper, suffix = _next_range(lower, monthly=False)
        assert _partition_upper_bound(f"activity_logs_{suffix}", "activity_logs") == upper
        lower = upper


def test_matches_existing_ddl_names():
    # names generated must equal what SQL/v1/ already created, not a new scheme
    assert _next_range(datetime(2027, 12, 1, tzinfo=UTC), monthly=True)[1] == "y2027m12"
    assert _next_range(datetime(2028, 1, 1, tzinfo=UTC), monthly=True)[1] == "y2028m01"
    assert _next_range(datetime(2027, 1, 1, tzinfo=UTC), monthly=False)[1] == "y2027"


def test_default_partition_never_parsed_as_dated():
    assert _partition_upper_bound("audit_logs_default", "audit_logs") is None


if __name__ == "__main__":
    test_monthly_roundtrip_across_year_boundary()
    test_yearly_roundtrip()
    test_matches_existing_ddl_names()
    test_default_partition_never_parsed_as_dated()
    print("ok")
