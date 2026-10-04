# Postgres NOTIFY replaces Redis (2026-10-04)

Schema: `SQL/v1/103_live_notify_tables.sql` (alembic 0056).
Code: `backend/app/core/live.py`, `backend/app/core/auth_session.py`,
`backend/app/workers/event_relay.py`, `backend/app/modules/notifications/router.py`.

No cache layer is part of this change.

## Why

Redis did three small jobs, all of which Postgres can do. Removing it removes a
service to run, a bill, and a second thing that can fail on every request.

| Job | Before (Redis) | After (Postgres) |
|---|---|---|
| Live push to the browser | `PUBLISH user:{id}:stream` | `pg_notify('user_stream', ...)` |
| Logged-out access tokens | key `revoked_jti:{jti}` with a TTL | table `ops.revoked_access_tokens` + in-memory copy |
| One-time stream tickets | key `sse_ticket:{ticket}` with a TTL | table `ops.sse_tickets` |

## Live push

```
action -> outbox event -> relay writes the notification row
                          and calls pg_notify in the SAME transaction
                                   |
       every API process holds ONE listening connection
                                   |
       it hands the message to that user's open streams -> popup
```

- The push is part of the transaction that saves the notification. It is
  delivered only on commit, so a popup always has a saved row behind it, and a
  rolled-back attempt pushes nothing.
- One listening connection per API process, however many users are connected.
- Payload: `"<recipient_id> <json>"`, the same JSON the browser already
  receives. NOTIFY payloads are capped at 8000 bytes, so title and body are
  cut to a fixed length; the notification row keeps the full text.

## Logged-out tokens

- Logout inserts the token id into `ops.revoked_access_tokens` and sends
  `pg_notify('token_revoked', ...)` in the same transaction.
- Every API process keeps the list in memory, so the check on every request
  is a dictionary lookup with no I/O.
- On (re)connect the listener loads the table, so a process that was away
  catches up.

## Stream tickets

- Only a SHA-256 hash of the ticket is stored.
- Opening the stream runs `DELETE ... RETURNING`, so a ticket works once.

## What is not guaranteed, and how it is covered

A NOTIFY is delivered once, to whoever is listening at that moment (Redis
pub/sub had the same limit).

| Gap | Cover |
|---|---|
| A process's listener drops | It reconnects (checked every 30 s), reloads the revoked-token list, and ends every open stream so each browser reconnects and reloads from the server |
| Browser was offline | On reconnect it reloads notifications and live lists (frontend `sse.ts`) |
| Token revoked on another process while this listener is down | Honoured once the listener is back; until then that token still works here (Redis had the same fail-open rule) |

## Row-level security

Both tables have RLS enabled and forced, like every table in this database. One
policy each, admitting only RLS role `system`, which the app sets for these
statements (same pattern as the relay and the sweepers). Tickets are consumed
before a user identity exists, so no per-user rule is possible.

## Requirements

- `DATABASE_URL` must be a direct database endpoint. LISTEN does not work
  through RDS Proxy or a transaction-mode pooler.
- One extra database connection per API process.

## Deploy order

1. Apply the SQL (safe under the old code: two new tables, nothing else).
2. Deploy the backend.
3. Remove `REDIS_URL` from the task secrets and retire ElastiCache once live
   popups are confirmed.

While old and new API instances run side by side during the rollout, a user
connected to an old instance misses pushes from a new one. Their notifications
are saved and appear on the next page load.
