# Appointment policy, paid-cancellation refunds, device-session reschedule window

Date: 2026-10-14. Baseline: `Anava_Appointments_Module_Current_State_v1.docx`, `Anava_Payments_Module_Current_State_v1.docx`.
Schema: `SQL/v1/118_appointment_policy_and_refunds.sql` (alembic `0064`).

## Decisions (signed off in chat, 2026-10-14)

| # | Decision |
|---|---|
| D1 | A paid appointment may be cancelled at any time. Refund applies only when the payment status is `paid` and the cancellation happens at least the clinic's cutoff (default 12 h) before the slot. |
| D2 | Late cancellation (inside the cutoff) is allowed, refund = 0. |
| D3 | Online (Razorpay) payments are refunded automatically through the Razorpay refund API. Cash payments get a refund recorded as due; front desk confirms when the cash is handed back. |
| D4 | Cutoff and refund percent live in the existing `reference.cancellation_policy_tiers` (platform default rows + per-clinic override set). Clinic admin may now write their own clinic's tiers; super admin writes the defaults and any clinic. Default seeded: 12 h -> 100 % for both session types, only if no default tier exists. |
| D5 | Patient reschedule notice (previously the constant 24 h) becomes `clinics.reschedule_min_hours`, editable by clinic admin / regional admin / super admin through `PATCH /clinics/{id}`. NULL = platform default `appointment_reschedule_min_hours` (config, 24). |
| D6 | A `device_session` in `no_show` or `missed` may only be rescheduled to the two days after the protocol's last other device session (Last+1, Last+2). Fixed, not configurable. If that window is already past, it starts today (still after the course ended). Applies to patient and staff. |
| D7 | Within one protocol, at most one device session per calendar day (claim-slot and reschedule). Reason shown to the user: two device sessions on the same day can cause overstimulation or injury. |
| D8 | Policy note: backend builds it from live settings (`GET /appointment-policy`); frontend gets a service function only. Screens are wired by the frontend owner. |
| D9 | Bug F-1 fixed: rescheduling an unpaid `selected` appointment clears the hold on the old row and carries the pending payment (same Razorpay order) to the new row. |

## Schema (118)

- `core.clinics.reschedule_min_hours NUMERIC(6,2) NULL CHECK (>= 0)`.
- `reference.cancellation_policy_tiers`: INSERT/UPDATE/DELETE policies admit `clinic_admin` for `clinic_id = rls_clinic_id()`.
- Seed default tiers `(appointment, 12, 100)`, `(device_session, 12, 100)` when no default tier exists for that session type.
- `core.payments`: `refund_status` (`pending` | `processed` | `failed`), `razorpay_refund_id` (unique), `refunded_at`; partial index on pending refunds.
- `core.payment_logs.status` CHECK gains `partially_refunded`.

## Refund state

| Field | Meaning |
|---|---|
| `cancellation_refund_percent/amount` | Computed at cancellation (unchanged). |
| `refund_status = pending` | Refund owed, not yet completed. Online: refund worker will call / poll Razorpay. Cash: waits for staff. |
| `refund_status = processed` | Money returned. `payments.status` becomes `refunded` (full) or `partially_refunded`. `refunded_at` set. |
| `refund_status = failed` | Razorpay refused or errored; logged in `payment_logs`. Staff resolve manually. |

Refund worker (`app/workers/refund_worker.py`, every 60 s, advisory lock 8412774, role `system`):
pending + online + no refund id -> adopt an existing Razorpay refund for the payment if one exists, else create one (`speed=normal`);
pending + refund id -> fetch status. Webhooks `refund.processed` / `refund.failed` finish it too, whichever lands first.

Revenue = `paid` + `partially_refunded`, net of processed refund amounts.

## API

| Verb | Path | Who | Purpose |
|---|---|---|---|
| GET | `/appointment-policy?clinic_id=` | all (patient: own clinic) | Policy note, structured values + text |
| GET | `/me/appointments/{id}/reschedule-options` | patient | Whether, and to which dates, this row can be moved, with the reason |
| GET | `/appointments/{id}/reschedule-options` | staff | Same with staff rules |
| PATCH | `/clinics/{id}` (+ `reschedule_min_hours`) | clinic/regional/super admin | Reschedule notice |
| POST/PATCH/DELETE | `/cancellation-policy-tiers` | super admin; clinic admin for own clinic | Refund cutoff and percent |
| PATCH | `/payments/{id}/status` `refunded` | super/regional/clinic admin, receptionist | Confirm cash refund handed back |

## Open points

- Staff-initiated cancellations still use the same tiers as patient cancellations (unchanged). If the clinic cancels, should the patient always get 100 %? Not decided.
- Cancelling an unpaid `selected` booking still leaves its `pending` payment (Payments finding P-2/P-8).
