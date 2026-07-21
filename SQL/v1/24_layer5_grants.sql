-- Layer 5 — grants on the 6 new tables. Existing ALTER DEFAULT PRIVILEGES
-- (18_grants.sql) already covers "future tables in compliance schema" for
-- anava_app/anava_readonly/anava_compliance, but that only applies to tables
-- created AFTER the default-privileges statement ran in the same session context
-- for the granting role — explicit grants here so there's no ambiguity.
GRANT SELECT, INSERT, UPDATE, DELETE ON
    compliance."erasure_requests",
    compliance."erasure_request_items",
    compliance."data_portability_requests",
    compliance."staff_termination_authorizations",
    compliance."compliance_incidents",
    compliance."manual_snapshots"
TO anava_app;

GRANT SELECT ON
    compliance."erasure_requests",
    compliance."erasure_request_items",
    compliance."data_portability_requests",
    compliance."staff_termination_authorizations",
    compliance."compliance_incidents",
    compliance."manual_snapshots"
TO anava_readonly;

GRANT SELECT, UPDATE ON
    compliance."erasure_requests",
    compliance."erasure_request_items",
    compliance."data_portability_requests",
    compliance."staff_termination_authorizations",
    compliance."compliance_incidents",
    compliance."manual_snapshots"
TO anava_compliance;
