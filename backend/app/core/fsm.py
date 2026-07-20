"""Shared status-transition guard. Each module still owns its own
from-status -> allowed-to-statuses dict (the data is genuinely different
per domain — clinic lifecycle, device orders, stock transfers) but the
enforcement was copy-pasted identically across store/inventory/admin
(found during the architecture review)."""

from __future__ import annotations

from app.core.exceptions import BusinessRuleError


def assert_transition(current: str, new: str, transitions: dict[str, set[str]], *, entity: str, code: str) -> None:
    if new not in transitions.get(current, set()):
        raise BusinessRuleError(f"Cannot transition {entity} from '{current}' to '{new}'", code=code)
