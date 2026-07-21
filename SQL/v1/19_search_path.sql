-- Generated from live production schema introspection (2026-07-20). Do not hand-edit column/RLS/trigger/function bodies — regenerate from source instead.

-- Every unqualified table reference inside the RLS policies, trigger functions,
-- and the view above resolves through this search_path. Table names are unique
-- across all 5 schemas (verified — no collisions), so this is safe.
ALTER DATABASE "Anava_App_v1" SET search_path = core, reference, compliance, analytics, ops, extensions, public;
