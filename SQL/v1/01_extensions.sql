-- Generated from live production schema introspection (2026-07-20). Do not hand-edit column/RLS/trigger/function bodies — regenerate from source instead.

CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS btree_gist SCHEMA extensions;
-- plpgsql is installed by default in every Postgres database, no action needed.
-- extensions schema is appended to search_path (19_search_path.sql) so gen_random_uuid()
-- and the GIST exclusion operators resolve without qualification, same as before.
