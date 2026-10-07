# Outbox relay reliability (2026-10-04)

Schema: `SQL/v1/102_outbox_retry_and_notification_read.sql` (alembic 0055).
Code: `backend/app/workers/event_relay.py`.

## Problems found

1. **No notification has been written since 2026-09-28.** The relay moved to the
   ordinary app login acting as RLS role `system` (SQL/v1/93). That file let
   `system` INSERT into `core.notifications` but not SELECT from it, and the
   relay writes with `INSERT ... RETURNING *`, which needs both. Every insert was
   rejected (reproduced on a clean database built from SQL/v1).
2. **Failures were invisible and permanent.** A failed event was still marked
   published. `publish_attempts` was never incremented, nothing recorded the
   error, and nothing retried.
3. **A live popup could be sent for a notification that was then rolled back**,
   because the push happened inside the transaction.

## Design

| Concern | Decision |
|---|---|
| Relay can read back its own insert | New policy `rls_notif_select_system` on `core.notifications` |
| Retry | A failed event stays unpublished; `publish_attempts` + 1, `last_error` recorded, `next_attempt_at` pushed out by a doubling delay (30 s, 1, 2, 4 min) |
| Give up | After 5 attempts `failed_at` is set. The event is never picked up again and never blocks the events behind it |
| Visibility | Each event row shows its own state: `published_at`, `failed_at`, `publish_attempts`, `last_error`. `/health/live` reports the count of failed events |
| No duplicates | Claiming the event, writing its notifications and marking it published are one transaction. A failed attempt rolls back to a savepoint, so a retry never leaves a second copy |
| Live push | Sent after the transaction commits, so a popup always has a saved row behind it |

## Schema change

`ops.outbox_events` gains three columns:

| Column | Type | Meaning |
|---|---|---|
| `next_attempt_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | Earliest time the relay may process the event |
| `failed_at` | `TIMESTAMPTZ` | Set when the relay gives up |
| `last_error` | `TEXT` | Most recent failure |

`idx_outbox_unpublished` is rebuilt to cover only events still waiting
(`published_at IS NULL AND failed_at IS NULL`), so it stays small however many
events the table holds.

## Deploy order

Apply the SQL first, then deploy the code. The SQL is safe under the old code
(new columns have defaults; the policy only adds access). The new code needs the
new columns.

## Not replayed

Events processed between 2026-09-28 and this fix produced no notification. They
are not replayed, the same decision as SQL/v1/92: users are notified for events
from now on, not sent a burst of stale ones.
