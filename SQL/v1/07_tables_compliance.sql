-- Generated from live production schema introspection (2026-07-20). Do not hand-edit column/RLS/trigger/function bodies — regenerate from source instead.

CREATE TABLE compliance."activity_logs" (
    "log_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "actor_id" UUID NOT NULL,
    "actor_role" TEXT NOT NULL,
    "request_id" TEXT,
    "category" TEXT NOT NULL,
    "event_type" TEXT NOT NULL,
    "entity_type" TEXT,
    "entity_id" UUID,
    "clinic_id" UUID,
    "region_id" UUID,
    "metadata" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "ip_address" INET,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE ("created_at");
-- Partitioned by monthly range on created_at. Initial partitions below;
-- ongoing partition creation ahead of the current date is an operational job (Layer 7),
-- not a one-time setup step — see ops/PARTITION_MAINTENANCE.md.
CREATE TABLE compliance."activity_logs_y2025m01" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
CREATE TABLE compliance."activity_logs_y2025m02" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
CREATE TABLE compliance."activity_logs_y2025m03" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
CREATE TABLE compliance."activity_logs_y2025m04" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE compliance."activity_logs_y2025m05" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE compliance."activity_logs_y2025m06" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE compliance."activity_logs_y2025m07" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE compliance."activity_logs_y2025m08" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE compliance."activity_logs_y2025m09" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE compliance."activity_logs_y2025m10" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE compliance."activity_logs_y2025m11" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE compliance."activity_logs_y2025m12" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
CREATE TABLE compliance."activity_logs_y2026m01" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE compliance."activity_logs_y2026m02" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE compliance."activity_logs_y2026m03" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE compliance."activity_logs_y2026m04" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE compliance."activity_logs_y2026m05" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE compliance."activity_logs_y2026m06" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE compliance."activity_logs_y2026m07" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE compliance."activity_logs_y2026m08" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE compliance."activity_logs_y2026m09" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE compliance."activity_logs_y2026m10" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE compliance."activity_logs_y2026m11" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE compliance."activity_logs_y2026m12" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE compliance."activity_logs_y2027m01" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');
CREATE TABLE compliance."activity_logs_y2027m02" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');
CREATE TABLE compliance."activity_logs_y2027m03" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');
CREATE TABLE compliance."activity_logs_y2027m04" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');
CREATE TABLE compliance."activity_logs_y2027m05" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-05-01') TO ('2027-06-01');
CREATE TABLE compliance."activity_logs_y2027m06" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-06-01') TO ('2027-07-01');
CREATE TABLE compliance."activity_logs_y2027m07" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-07-01') TO ('2027-08-01');
CREATE TABLE compliance."activity_logs_y2027m08" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-08-01') TO ('2027-09-01');
CREATE TABLE compliance."activity_logs_y2027m09" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-09-01') TO ('2027-10-01');
CREATE TABLE compliance."activity_logs_y2027m10" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-10-01') TO ('2027-11-01');
CREATE TABLE compliance."activity_logs_y2027m11" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-11-01') TO ('2027-12-01');
CREATE TABLE compliance."activity_logs_y2027m12" PARTITION OF compliance."activity_logs"
    FOR VALUES FROM ('2027-12-01') TO ('2028-01-01');
CREATE TABLE compliance."activity_logs_default" PARTITION OF compliance."activity_logs" DEFAULT;


CREATE TABLE compliance."audit_logs" (
    "log_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "table_name" TEXT NOT NULL,
    "operation" TEXT NOT NULL,
    "record_id" TEXT,
    "old_data" JSONB,
    "new_data" JSONB,
    "changed_by" UUID,
    "clinic_id" UUID,
    "ip_address" INET,
    "request_id" TEXT,
    "changed_at" TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE ("changed_at");
-- Partitioned by monthly range on changed_at. Initial partitions below;
-- ongoing partition creation ahead of the current date is an operational job (Layer 7),
-- not a one-time setup step — see ops/PARTITION_MAINTENANCE.md.
CREATE TABLE compliance."audit_logs_y2025m01" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
CREATE TABLE compliance."audit_logs_y2025m02" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
CREATE TABLE compliance."audit_logs_y2025m03" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
CREATE TABLE compliance."audit_logs_y2025m04" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE compliance."audit_logs_y2025m05" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE compliance."audit_logs_y2025m06" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE compliance."audit_logs_y2025m07" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE compliance."audit_logs_y2025m08" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE compliance."audit_logs_y2025m09" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE compliance."audit_logs_y2025m10" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE compliance."audit_logs_y2025m11" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE compliance."audit_logs_y2025m12" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
CREATE TABLE compliance."audit_logs_y2026m01" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE compliance."audit_logs_y2026m02" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE compliance."audit_logs_y2026m03" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE compliance."audit_logs_y2026m04" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE compliance."audit_logs_y2026m05" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE compliance."audit_logs_y2026m06" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE compliance."audit_logs_y2026m07" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE compliance."audit_logs_y2026m08" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE compliance."audit_logs_y2026m09" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE compliance."audit_logs_y2026m10" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE compliance."audit_logs_y2026m11" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE compliance."audit_logs_y2026m12" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE compliance."audit_logs_y2027m01" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');
CREATE TABLE compliance."audit_logs_y2027m02" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');
CREATE TABLE compliance."audit_logs_y2027m03" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');
CREATE TABLE compliance."audit_logs_y2027m04" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');
CREATE TABLE compliance."audit_logs_y2027m05" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-05-01') TO ('2027-06-01');
CREATE TABLE compliance."audit_logs_y2027m06" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-06-01') TO ('2027-07-01');
CREATE TABLE compliance."audit_logs_y2027m07" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-07-01') TO ('2027-08-01');
CREATE TABLE compliance."audit_logs_y2027m08" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-08-01') TO ('2027-09-01');
CREATE TABLE compliance."audit_logs_y2027m09" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-09-01') TO ('2027-10-01');
CREATE TABLE compliance."audit_logs_y2027m10" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-10-01') TO ('2027-11-01');
CREATE TABLE compliance."audit_logs_y2027m11" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-11-01') TO ('2027-12-01');
CREATE TABLE compliance."audit_logs_y2027m12" PARTITION OF compliance."audit_logs"
    FOR VALUES FROM ('2027-12-01') TO ('2028-01-01');
CREATE TABLE compliance."audit_logs_default" PARTITION OF compliance."audit_logs" DEFAULT;


CREATE TABLE compliance."consent_records" (
    "consent_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "consent_type" TEXT NOT NULL,
    "template_id" UUID NOT NULL,
    "patient_id" UUID,
    "staff_id" UUID,
    "clinic_id" UUID,
    "status" TEXT NOT NULL DEFAULT 'pending'::text,
    "signed_at" TIMESTAMPTZ,
    "signed_by" UUID,
    "witness_id" UUID,
    "ip_address" INET,
    "signature_data" TEXT,
    "pdf_s3_key" TEXT,
    "content_hash_at_signing" TEXT,
    "revoked_at" TIMESTAMPTZ,
    "revoked_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "region_id" UUID
);

