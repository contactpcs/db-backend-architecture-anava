-- Generated from live production schema introspection (2026-07-20). Do not hand-edit column/RLS/trigger/function bodies — regenerate from source instead.

CREATE TABLE ops."alembic_version" (
    "version_num" VARCHAR(32) NOT NULL
);


CREATE TABLE ops."outbox_events" (
    "outbox_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "aggregate_type" TEXT NOT NULL,
    "aggregate_id" TEXT NOT NULL,
    "event_type" TEXT NOT NULL,
    "payload" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "published_at" TIMESTAMPTZ,
    "publish_attempts" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE ops."schema_migrations" (
    "version" TEXT NOT NULL,
    "applied_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

