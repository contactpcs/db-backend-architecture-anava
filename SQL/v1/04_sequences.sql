-- Generated from live production schema introspection (2026-07-20). Do not hand-edit column/RLS/trigger/function bodies — regenerate from source instead.

-- Matches source definition exactly (start_value=10001). This is a FRESH sequence
-- for the empty anava_v1 structure. At Phase D (real data migration), this sequence's
-- current value must be advanced past the highest imported MRN before the app writes
-- through it again — that is a migration-time step, not handled by this DDL.
CREATE SEQUENCE core.mrn_seq
    START WITH 10001
    INCREMENT BY 1
    MINVALUE 1
    NO CYCLE;
