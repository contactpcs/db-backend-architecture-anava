from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_helpers import fetch_one, fetch_optional


class PaymentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        session_id,
        order_id,
        amount: float,
        currency: str,
        idempotency_key: str,
        razorpay_order_id: str | None = None,
        appointment_id=None,
        base_fee_amount: float | None = None,
        platform_fee_percent: float | None = None,
        platform_fee_amount: float | None = None,
    ) -> dict:
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO payments (session_id, order_id, appointment_id, amount, currency, idempotency_key, razorpay_order_id, "
                "base_fee_amount, platform_fee_percent, platform_fee_amount) "
                "VALUES (:session_id, :order_id, :appointment_id, :amount, :currency, :idem, :rzp_order, "
                ":base_fee_amount, :platform_fee_percent, :platform_fee_amount) RETURNING *"
            ),
            {
                "session_id": str(session_id) if session_id else None,
                "order_id": str(order_id) if order_id else None,
                "appointment_id": str(appointment_id) if appointment_id else None,
                "amount": amount,
                "currency": currency,
                "idem": idempotency_key,
                "rzp_order": razorpay_order_id,
                "base_fee_amount": base_fee_amount,
                "platform_fee_percent": platform_fee_percent,
                "platform_fee_amount": platform_fee_amount,
            },
        )

    # ── payment_logs — append-only event history (66_payment_logs.sql) ──────

    async def log_event(
        self,
        payment_id: UUID,
        *,
        status: str,
        amount: float,
        currency: str,
        source: str,
        payment_method: str | None = None,
        razorpay_order_id: str | None = None,
        razorpay_payment_id: str | None = None,
        failure_code: str | None = None,
        failure_reason: str | None = None,
        gateway_event: str | None = None,
        gateway_response: dict | None = None,
        changed_by: UUID | None = None,
        changed_by_role: str | None = None,
    ) -> dict:
        return await fetch_one(
            self.session,
            text(
                "INSERT INTO payment_logs (payment_id, status, amount, currency, payment_method, razorpay_order_id, "
                "razorpay_payment_id, failure_code, failure_reason, source, gateway_event, gateway_response, changed_by, changed_by_role) "
                "VALUES (:payment_id, :status, :amount, :currency, :payment_method, :razorpay_order_id, :razorpay_payment_id, "
                ":failure_code, :failure_reason, :source, :gateway_event, CAST(:gateway_response AS JSONB), :changed_by, :changed_by_role) "
                "RETURNING *"
            ),
            {
                "payment_id": str(payment_id),
                "status": status,
                "amount": amount,
                "currency": currency,
                "payment_method": payment_method,
                "razorpay_order_id": razorpay_order_id,
                "razorpay_payment_id": razorpay_payment_id,
                "failure_code": failure_code,
                "failure_reason": failure_reason,
                "source": source,
                "gateway_event": gateway_event,
                "gateway_response": json.dumps(gateway_response or {}),
                "changed_by": str(changed_by) if changed_by else None,
                "changed_by_role": changed_by_role,
            },
        )

    async def list_for_payment(self, payment_id: UUID) -> list[dict]:
        rows = (
            (await self.session.execute(text("SELECT * FROM payment_logs WHERE payment_id = :id ORDER BY created_at ASC"), {"id": str(payment_id)}))
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def list_by_status(
        self,
        *,
        status: str | None,
        clinic_id: UUID | None,
        region_id: UUID | None,
        date_from=None,
        date_to=None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Scoped the same way payments/history is (PaymentService._history_scope)
        — a clinic_admin/receptionist sees their clinic's failed/refunded/etc
        payments, a regional_admin their region's, super_admin everything."""
        scope_sql, scope_params = self._scope_clause(clinic_id=clinic_id, region_id=region_id)
        where = ["1=1"]
        params: dict = {**scope_params, "limit": limit, "offset": offset}
        if status:
            where.append("pl.status = :status")
            params["status"] = status
        if date_from:
            where.append("pl.created_at >= :date_from")
            params["date_from"] = date_from
        if date_to:
            where.append("pl.created_at < :date_to")
            params["date_to"] = date_to

        query = (
            "SELECT pl.*, "
            "pp.first_name || ' ' || pp.last_name AS patient_name, "
            "c.clinic_name "
            "FROM payment_logs pl "
            "JOIN payments p ON p.payment_id = pl.payment_id "
            "LEFT JOIN appointments a ON a.appointment_id = p.appointment_id "
            "LEFT JOIN store_orders so ON so.order_id = p.order_id "
            "LEFT JOIN profiles pp ON pp.id = COALESCE(a.patient_id, so.patient_id) "
            "LEFT JOIN clinics c ON c.clinic_id = COALESCE(a.clinic_id, so.clinic_id) "
            f"WHERE {' AND '.join(where)} {scope_sql} "
            "ORDER BY pl.created_at DESC LIMIT :limit OFFSET :offset"
        )
        rows = (await self.session.execute(text(query), params)).mappings().all()
        return [dict(r) for r in rows]

    async def set_cancellation_refund(self, payment_id: UUID, *, refund_percent: float, refund_amount: float) -> dict | None:
        return await fetch_optional(
            self.session,
            text(
                "UPDATE payments SET cancellation_refund_percent = :pct, cancellation_refund_amount = :amt "
                "WHERE payment_id = :id RETURNING *"
            ),
            {"pct": refund_percent, "amt": refund_amount, "id": str(payment_id)},
        )

    async def get_by_razorpay_order_id(self, razorpay_order_id: str) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM payments WHERE razorpay_order_id = :id"), {"id": razorpay_order_id})

    async def get_for_appointment(self, appointment_id) -> dict | None:
        """Most recent payment against an appointment — used to resume an
        in-progress mock checkout instead of spawning a duplicate order on
        every retry, same idea as get_for_session below."""
        return await fetch_optional(
            self.session,
            text("SELECT * FROM payments WHERE appointment_id = :id ORDER BY created_at DESC LIMIT 1"),
            {"id": str(appointment_id)},
        )

    async def get(self, payment_id: UUID) -> dict | None:
        return await fetch_optional(self.session, text("SELECT * FROM payments WHERE payment_id = :id"), {"id": str(payment_id)})

    async def get_owner_profile_id(self, payment_id: UUID) -> str | None:
        """A payment has no patient_id of its own — same two-hop join as
        list_by_clinic, since it's for either a store order or an
        appointment, and both carry patient_id (already resolved to
        profiles.id at creation time, see resolve_patient_profile_id). Used
        to enforce assert_owns_profile on the single-payment read (no
        ownership check existed at all here before the eng review — see
        get_payment)."""
        row = await fetch_optional(
            self.session,
            text(
                "SELECT COALESCE(so.patient_id, appt.patient_id) AS owner_profile_id "
                "FROM payments p "
                "LEFT JOIN store_orders so ON so.order_id = p.order_id "
                "LEFT JOIN appointments appt ON appt.appointment_id = p.appointment_id "
                "WHERE p.payment_id = :id"
            ),
            {"id": str(payment_id)},
        )
        return row["owner_profile_id"] if row else None

    async def get_for_session(self, session_id: UUID) -> dict | None:
        return await fetch_optional(
            self.session,
            text("SELECT * FROM payments WHERE session_id = :id ORDER BY created_at DESC LIMIT 1"),
            {"id": str(session_id)},
        )

    async def get_for_order(self, order_id: UUID) -> dict | None:
        """Most recent payment against a store order — same reuse-existing
        pattern as get_for_appointment, so retrying 'Pay Now' doesn't spawn a
        duplicate Razorpay order."""
        return await fetch_optional(
            self.session,
            text("SELECT * FROM payments WHERE order_id = :id ORDER BY created_at DESC LIMIT 1"),
            {"id": str(order_id)},
        )

    async def get_clinic_id(self, payment_id: UUID) -> str | None:
        """Same two-hop join as list_by_clinic/get_owner_profile_id — a
        payment has no clinic_id of its own, it's for either a store order or
        an appointment, and both carry clinic_id."""
        row = await fetch_optional(
            self.session,
            text(
                "SELECT COALESCE(so.clinic_id, appt.clinic_id) AS clinic_id "
                "FROM payments p "
                "LEFT JOIN store_orders so ON so.order_id = p.order_id "
                "LEFT JOIN appointments appt ON appt.appointment_id = p.appointment_id "
                "WHERE p.payment_id = :id"
            ),
            {"id": str(payment_id)},
        )
        return row["clinic_id"] if row else None

    async def list_by_clinic(self, clinic_id: UUID) -> list[dict]:
        # payments has no clinic_id of its own — scoped via a two-hop join,
        # since a payment is for either an appointment or a store order,
        # and both of those carry clinic_id.
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT p.* FROM payments p "
                        "LEFT JOIN store_orders so ON so.order_id = p.order_id "
                        "LEFT JOIN appointments appt ON appt.appointment_id = p.appointment_id "
                        "WHERE COALESCE(so.clinic_id, appt.clinic_id) = :clinic_id "
                        "ORDER BY p.created_at DESC"
                    ),
                    {"clinic_id": str(clinic_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def list_for_patient(self, patient_id: UUID) -> list[dict]:
        # Same two-hop join as list_by_clinic, scoped to patient_id instead of
        # clinic_id — appointment_type/appointment_date come along so the
        # billing-history screen needs no per-row follow-up fetch.
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT p.*, appt.appointment_type, appt.appointment_date FROM payments p "
                        "LEFT JOIN store_orders so ON so.order_id = p.order_id "
                        "LEFT JOIN appointments appt ON appt.appointment_id = p.appointment_id "
                        "WHERE COALESCE(so.patient_id, appt.patient_id) = :patient_id "
                        "ORDER BY p.created_at DESC"
                    ),
                    {"patient_id": str(patient_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def set_status(
        self, payment_id: UUID, *, status: str, payment_method, waived_by, waived_reason, razorpay_payment_id=None
    ) -> dict | None:
        paid_at_clause = ", paid_at = NOW()" if status == "paid" else ""
        rzp_clause = ", razorpay_payment_id = COALESCE(:rzp_payment, razorpay_payment_id)"
        return await fetch_optional(
            self.session,
            text(
                f"UPDATE payments SET status = :status, payment_method = :method, waived_by = :waived_by, "
                f"waived_reason = :reason {paid_at_clause} {rzp_clause} WHERE payment_id = :id RETURNING *"
            ),
            {
                "status": status,
                "method": payment_method,
                "waived_by": str(waived_by) if waived_by else None,
                "reason": waived_reason,
                "id": str(payment_id),
                "rzp_payment": razorpay_payment_id,
            },
        )

    @staticmethod
    def _scope_clause(*, clinic_id, region_id) -> tuple[str, dict]:
        """Shared by list_history/revenue_summary/patient_revenue_totals — a
        clinic_admin/receptionist row pins to their own clinic, a
        regional_admin row to every clinic in their region (via the clinic
        join both queries already carry), a super_admin passes both None and
        gets every clinic. Exactly one of clinic_id/region_id is ever set by
        the caller (PaymentService resolves that from ctx.role)."""
        if clinic_id:
            return "AND COALESCE(a.clinic_id, so.clinic_id) = :scope_clinic_id", {"scope_clinic_id": str(clinic_id)}
        if region_id:
            return "AND c.region_id = :scope_region_id", {"scope_region_id": str(region_id)}
        return "", {}

    # payments has no clinic_id/patient_id of its own — every history query
    # needs this same two-hop join (appointment or store order) to hydrate
    # who/what/where, so it's factored out once rather than repeated three
    # times with a chance to drift.
    _HISTORY_BASE = (
        "FROM payments p "
        "LEFT JOIN appointments a ON a.appointment_id = p.appointment_id "
        "LEFT JOIN store_orders so ON so.order_id = p.order_id "
        "LEFT JOIN profiles pp ON pp.id = COALESCE(a.patient_id, so.patient_id) "
        "LEFT JOIN profiles dp ON dp.id = a.doctor_id "
        "LEFT JOIN clinics c ON c.clinic_id = COALESCE(a.clinic_id, so.clinic_id) "
    )

    async def list_history(
        self,
        *,
        clinic_id: UUID | None,
        region_id: UUID | None,
        status: str | None = None,
        search: str | None = None,
        date_from=None,
        date_to=None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        scope_sql, scope_params = self._scope_clause(clinic_id=clinic_id, region_id=region_id)
        where = ["1=1"]
        params: dict = {**scope_params, "limit": limit, "offset": offset}
        if status:
            where.append("p.status = :status")
            params["status"] = status
        if search:
            where.append("pp.first_name || ' ' || pp.last_name ILIKE :search")
            params["search"] = f"%{search}%"
        if date_from:
            where.append("p.created_at >= :date_from")
            params["date_from"] = date_from
        if date_to:
            where.append("p.created_at < :date_to")
            params["date_to"] = date_to

        query = (
            "SELECT p.*, "
            "pp.first_name || ' ' || pp.last_name AS patient_name, "
            "COALESCE(a.patient_id, so.patient_id) AS patient_id, "
            "COALESCE(a.clinic_id, so.clinic_id) AS effective_clinic_id, "
            "c.clinic_name, "
            "dp.first_name || ' ' || dp.last_name AS doctor_name, "
            "COALESCE(a.appointment_type, so.order_type) AS purpose, "
            "a.appointment_date, a.start_time AS appointment_start_time, "
            "a.status AS appointment_status, a.completed_at AS appointment_completed_at "
            f"{self._HISTORY_BASE}"
            f"WHERE {' AND '.join(where)} {scope_sql} "
            "ORDER BY p.created_at DESC LIMIT :limit OFFSET :offset"
        )
        rows = (await self.session.execute(text(query), params)).mappings().all()
        return [dict(r) for r in rows]

    async def revenue_summary(
        self,
        *,
        clinic_id: UUID | None,
        region_id: UUID | None,
        group_by: str,
        date_from=None,
        date_to=None,
    ) -> list[dict]:
        """SUM(amount) of paid payments bucketed by date_trunc(group_by,
        paid_at). group_by is validated by the service against a literal
        whitelist before it ever reaches here — never build this string from
        unvalidated input, date_trunc's first argument can't be parameterized."""
        scope_sql, scope_params = self._scope_clause(clinic_id=clinic_id, region_id=region_id)
        where = ["p.status = 'paid'", "p.paid_at IS NOT NULL"]
        params: dict = {**scope_params}
        if date_from:
            where.append("p.paid_at >= :date_from")
            params["date_from"] = date_from
        if date_to:
            where.append("p.paid_at < :date_to")
            params["date_to"] = date_to

        query = (
            f"SELECT date_trunc('{group_by}', p.paid_at) AS period, SUM(p.amount) AS total, COUNT(*) AS payment_count "
            f"{self._HISTORY_BASE}"
            f"WHERE {' AND '.join(where)} {scope_sql} "
            "GROUP BY period ORDER BY period ASC"
        )
        rows = (await self.session.execute(text(query), params)).mappings().all()
        return [dict(r) for r in rows]

    async def revenue_summary_by_purpose(
        self,
        *,
        clinic_id: UUID | None,
        region_id: UUID | None,
        group_by: str,
        date_from=None,
        date_to=None,
    ) -> list[dict]:
        """Same bucketing as revenue_summary, split one row per (period,
        purpose) instead of collapsing every purpose into one line — this is
        what lets the frontend draw one line per appointment_type/
        device_session instead of a single blended total."""
        scope_sql, scope_params = self._scope_clause(clinic_id=clinic_id, region_id=region_id)
        where = ["p.status = 'paid'", "p.paid_at IS NOT NULL"]
        params: dict = {**scope_params}
        if date_from:
            where.append("p.paid_at >= :date_from")
            params["date_from"] = date_from
        if date_to:
            where.append("p.paid_at < :date_to")
            params["date_to"] = date_to

        query = (
            f"SELECT date_trunc('{group_by}', p.paid_at) AS period, "
            "COALESCE(a.appointment_type, so.order_type, 'other') AS purpose, "
            "SUM(p.amount) AS total, COUNT(*) AS payment_count "
            f"{self._HISTORY_BASE}"
            f"WHERE {' AND '.join(where)} {scope_sql} "
            "GROUP BY period, purpose ORDER BY period ASC, purpose ASC"
        )
        rows = (await self.session.execute(text(query), params)).mappings().all()
        return [dict(r) for r in rows]

    async def patient_revenue_totals(
        self,
        *,
        clinic_id: UUID | None,
        region_id: UUID | None,
        date_from=None,
        date_to=None,
        limit: int = 20,
    ) -> list[dict]:
        scope_sql, scope_params = self._scope_clause(clinic_id=clinic_id, region_id=region_id)
        where = ["p.status = 'paid'"]
        params: dict = {**scope_params, "limit": limit}
        if date_from:
            where.append("p.paid_at >= :date_from")
            params["date_from"] = date_from
        if date_to:
            where.append("p.paid_at < :date_to")
            params["date_to"] = date_to

        query = (
            "SELECT COALESCE(a.patient_id, so.patient_id) AS patient_id, "
            "pp.first_name || ' ' || pp.last_name AS patient_name, "
            "SUM(p.amount) AS total_paid, COUNT(*) AS payment_count "
            f"{self._HISTORY_BASE}"
            f"WHERE {' AND '.join(where)} {scope_sql} "
            "GROUP BY COALESCE(a.patient_id, so.patient_id), pp.first_name, pp.last_name "
            "ORDER BY total_paid DESC LIMIT :limit"
        )
        rows = (await self.session.execute(text(query), params)).mappings().all()
        return [dict(r) for r in rows]
