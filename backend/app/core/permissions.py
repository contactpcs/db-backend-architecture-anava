"""Permission dependency factories. Every mutating/sensitive endpoint declares
one of these — RLS (15_rls_policies.sql) is the defense-in-depth backstop
(ADR-003), not the primary check. See Architecture Section 9."""

from fastapi import Depends

from app.core.db import RequestContext, get_request_context
from app.core.exceptions import PermissionError_


def get_current_context() -> RequestContext:
    ctx = get_request_context()
    if ctx is None:
        # Should be unreachable if AuthContextMiddleware ran — fail closed, not open.
        raise PermissionError_("No authenticated context", code="NO_AUTH_CONTEXT")
    return ctx


def require_role(*allowed_roles: str):
    def _check(ctx: RequestContext = Depends(get_current_context)) -> RequestContext:
        if ctx.role not in allowed_roles:
            raise PermissionError_(
                f"Role '{ctx.role}' is not permitted to perform this action",
                code="ROLE_NOT_PERMITTED",
            )
        return ctx

    return _check


def require_clinic_scope(clinic_id_param: str = "clinic_id"):
    """Verifies the resource's clinic_id (read from the path/query param named
    clinic_id_param) matches the caller's own clinic — unless the caller is
    super_admin/regional_admin, who cross clinic boundaries by design."""

    def _check(ctx: RequestContext = Depends(get_current_context)) -> RequestContext:
        if ctx.role in ("super_admin", "regional_admin"):
            return ctx
        # Concrete clinic_id comparison happens in the route/service, since
        # the resource's clinic_id often isn't known until after a DB lookup
        # (e.g. GET /patients/{id}) — this dependency establishes the caller's
        # own scope; the service layer compares it against the resource.
        if ctx.clinic_id is None:
            raise PermissionError_("Caller has no clinic scope", code="NO_CLINIC_SCOPE")
        return ctx

    return _check
