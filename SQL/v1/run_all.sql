-- Generated from live production schema introspection (2026-07-20). Do not hand-edit column/RLS/trigger/function bodies — regenerate from source instead.

-- Run in this order. Each file assumes prior files already executed.
-- psql -h <rds-endpoint> -U postgres -d Anava_App_v1 -f SQL/v1/00_run_all.sql

\i 00_schemas.sql
\i 01_extensions.sql
\i 02_roles.sql
\i 03_enum_types.sql
\i 04_sequences.sql
\i 05_tables_core.sql
\i 06_tables_reference.sql
\i 07_tables_compliance.sql
\i 08_tables_ops.sql
\i 09_primary_keys.sql
\i 10_unique_constraints.sql
\i 11_foreign_keys.sql
\i 12_indexes.sql
\i 12b_appointments_overlap_guard.sql
\i 13_views.sql
\i 14_functions.sql
\i 15_triggers.sql
\i 16_rls_enable.sql
\i 17_rls_policies.sql
\i 18_grants.sql
\i 19_search_path.sql