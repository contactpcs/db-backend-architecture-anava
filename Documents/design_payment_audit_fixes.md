# Payment audit fixes — 2026-10-16

Source: payment audit of 2026-10-16 (H1–H3, M1–M9, L1–L8). Schema: `SQL/v1/121_payment_integrity_and_rls.sql` (alembic `0067`).

| # | Problem | Fix |
|---|---|---|
| H1 | A payment captured at Razorpay is recorded only if the browser verify or the webhook arrives. If both are missed, the booking is cancelled at hold expiry and the money is never recorded or refunded. | New `payment_reconciler` worker (every 2 min): asks Razorpay about every recent `pending` or `failed` online payment. A captured one is confirmed through the normal path (booking confirmed if still awaiting payment, otherwise refunded automatically). |
| H2 | Refund worker marked a refund `failed` on any error, including network blips while Razorpay was still refunding; staff then refunded by hand as well. | Only a Razorpay rejection (`BadRequestError`) marks `failed`. Timeouts and server errors leave it `pending` and retry next pass. |
| H3 | Cash taken (or fee waived) while an online checkout was still open left that Razorpay order payable; a later online payment was swallowed. | Cash closes the open online order (marked `failed`) and is recorded as its own payment. A capture on a closed or waived order is treated as orphaned and refunded in full. |
| M1 | Without Razorpay keys every signature check passed. | Outside `local`/`test`, the app refuses to start without key id, key secret and webhook secret (when payment is required); signature checks without keys fail closed outside `local`/`test`. |
| M2 | Captured amount never compared with the charged amount. | Webhook and reconciler compare amount and currency; a mismatch is logged and not confirmed. |
| M3 | Unknown-order webhook logged the full Razorpay body (payer email/phone/VPA). | Logs event and ids only. |
| M4 | DB policies let any staff role read every clinic's payment logs, and clinic/regional admins every clinic's payments. | `payments` SELECT scoped by clinic / region; `payment_logs` visible only where the payment is (SQL 121). |
| M5 | Manual status changes had no rules; non-admin roles got a 500. | Allowed manual changes: pending → paid/failed/waived, failed → paid/waived, paid or partially refunded → refunded. Paid/failed set by clinic or super admin only (403 otherwise). A manual full refund records the refund amount and timeline. |
| M7 | Razorpay checkout had no time limit; patients paid after the hold expired. | Checkout `timeout` = seconds left on the hold; expired holds show a message instead of opening checkout. |
| L1 | Webhook parsed JSON before checking the signature. | Signature first; malformed body → 400. |
| L2 | Failed browser-verify security log rolled back with the request. | Written in its own committed transaction. |
| L3 | Receipt said "Cancellation Refund Due" after refunding; device-session receipts used a fallback item name. | "Refunded" / "Refund due" by refund status; device price name used. |
| L4 | Reused pending order kept an old price. | A pending order whose amount differs from the current price is closed and a new one created. |
| L5 | Razorpay receipt id 41 chars (limit 40). | `appt-<uuid hex>` (37), `order-<uuid hex>` (38). |
| L6 | Float money math; no CHECK on `payments.status`. | Decimal arithmetic (half-up to paise); status CHECK in SQL 121. |
| L7 | Simultaneous verify + webhook could 4xx the webhook once. | `mark_paid` re-reads the appointment after a no-op update. |

Not changed (decisions or larger builds): M6 GST/tax invoices (finance), M8 store-order payment flow (feature build), M9 staff-cancel refund rule and platform-fee refund (product decision), L8 alembic stamp (run `alembic stamp 0067` with migration credentials).
