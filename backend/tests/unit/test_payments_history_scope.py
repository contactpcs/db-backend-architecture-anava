"""_history_scope (payments/service.py) is what actually stops a doctor or a
clinic_admin from widening their view to another clinic's revenue on the new
/payments/history, /payments/revenue-summary, /payments/patient-totals
endpoints — the router's role list is the first gate, this is the second.
_scope_clause (payments/repository.py) is the SQL half of the same contract:
exactly one of clinic_id/region_id ever produces a WHERE fragment."""

from uuid import uuid4

import pytest

from app.core.db import RequestContext
from app.modules.payments.repository import PaymentRepository
from app.modules.payments.service import _history_scope

CLINIC_ID = str(uuid4())
REGION_ID = str(uuid4())


def _ctx(role: str, **kw) -> RequestContext:
    return RequestContext(user_id=str(uuid4()), role=role, clinic_id=kw.get("clinic_id"), region_id=kw.get("region_id"))


def test_super_admin_gets_no_scope():
    clinic_id, region_id = _history_scope(_ctx("super_admin"))
    assert clinic_id is None and region_id is None


def test_regional_admin_scoped_to_their_region_only():
    clinic_id, region_id = _history_scope(_ctx("regional_admin", region_id=REGION_ID))
    assert clinic_id is None
    assert str(region_id) == REGION_ID


@pytest.mark.parametrize("role", ["clinic_admin", "receptionist"])
def test_clinic_pinned_roles_scoped_to_their_clinic_only(role):
    clinic_id, region_id = _history_scope(_ctx(role, clinic_id=CLINIC_ID))
    assert region_id is None
    assert str(clinic_id) == CLINIC_ID


def test_scope_clause_clinic_filter_is_mutually_exclusive_with_region():
    sql, params = PaymentRepository._scope_clause(clinic_id=CLINIC_ID, region_id=REGION_ID)
    assert "clinic_id" in sql
    assert "scope_clinic_id" in params
    assert "scope_region_id" not in params


def test_scope_clause_no_scope_when_neither_set():
    sql, params = PaymentRepository._scope_clause(clinic_id=None, region_id=None)
    assert sql == ""
    assert params == {}
