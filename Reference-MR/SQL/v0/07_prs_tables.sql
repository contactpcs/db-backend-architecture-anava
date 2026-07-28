-- ============================================================
-- Anava Clinic — DB Schema
-- File 07: PRS Tables
--
-- BASE RULE: Keep v6 structure exactly — application code and
-- seed scripts (seed_scales.py from PRS_DET.xlsx) depend on it.
--
-- CHANGES FROM v6:
--   prs_scales            + ADD applicable_for (new Anava field)
--   prs_assessment_instances:
--                         + ADD assessment_stage  (new Anava field)
--                         + ADD cycle_id          (new Anava field)
--                         + ADD administered_by   (new Anava field)
--                         ~ visit_id renamed → session_id
--                           FK updated: sessions(session_id) not sessions(id)
--                         ~ patient_id FK → profiles(id) not patients(id)
--                           (Anava universal table is profiles, not patients)
--   patient_scale_assignments — BRAND NEW table (Anava only)
--
-- DROPPED vs v6:
--   assessment_permissions — replaced by assessment_protocol_requests
--                            (in 06_clinical_tables.sql)
--
-- SEED DATA (questions, options):
--   Run backend/scripts/seed_scales.py after schema is applied.
--   Diseases + scales + disease_scale_map: see 16_seed_data.sql.
-- ============================================================


-- ============================================================
-- ENUMS (v6, kept as-is)
-- ============================================================

DO $$ BEGIN
    CREATE TYPE assessment_taken_by AS ENUM ('patient', 'doctor_on_behalf');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- ============================================================
-- prs_diseases (v6, TEXT PK, placed in 02_core_tables.sql)
-- Referenced here for documentation only.
-- ============================================================
-- Already created in 02_core_tables.sql


-- ============================================================
-- prs_scales (v6 + Anava: ADD applicable_for)
-- TEXT PK: e.g. 'EQ-5D-5L/2026'
-- applicable_for: which PRS stage this scale belongs to
-- ============================================================
CREATE TABLE prs_scales (
    scale_id          TEXT PRIMARY KEY,
    scale_code        TEXT NOT NULL UNIQUE,
    scale_name        TEXT NOT NULL,
    is_common_scale   BOOLEAN NOT NULL DEFAULT FALSE,
    num_diseases_used INTEGER NOT NULL DEFAULT 1,
    -- Anava addition: which stage administers this scale
    applicable_for    TEXT NOT NULL DEFAULT 'main_clinical'
                          CHECK (applicable_for IN (
                              'general_registration',
                              'main_clinical',
                              'followup',
                              'all'
                          )),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- prs_disease_scale_map (v6, unchanged)
-- Ordered mapping: which scales belong to each disease
-- TEXT PK: e.g. 'Depression/Anxiety/EQ-5D-5L'
-- ============================================================
CREATE TABLE prs_disease_scale_map (
    ds_map_id     TEXT PRIMARY KEY,
    disease_id    TEXT NOT NULL REFERENCES prs_diseases(disease_id) ON DELETE CASCADE,
    scale_id      TEXT NOT NULL REFERENCES prs_scales(scale_id) ON DELETE CASCADE,
    display_order INTEGER NOT NULL DEFAULT 0,
    is_required   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (disease_id, scale_id)
);


-- ============================================================
-- prs_questions (v6, unchanged)
-- TEXT PK: e.g. 'PDSS/004'
-- answer_type extended with 'table' for complex grid questions
-- ============================================================
CREATE TABLE prs_questions (
    question_id     TEXT PRIMARY KEY,
    question_code   TEXT NOT NULL UNIQUE,
    disease_id      TEXT REFERENCES prs_diseases(disease_id) ON DELETE SET NULL,
    scale_id        TEXT REFERENCES prs_scales(scale_id) ON DELETE SET NULL,
    ds_map_id       TEXT REFERENCES prs_disease_scale_map(ds_map_id) ON DELETE SET NULL,
    question_text   TEXT NOT NULL,
    answer_type     TEXT NOT NULL CHECK (answer_type IN (
                        'likert', 'radio', 'slider', 'checkbox',
                        'text', 'number', 'table'
                    )),
    min_value       NUMERIC,
    max_value       NUMERIC,
    is_required     BOOLEAN NOT NULL DEFAULT TRUE,
    skip_logic      TEXT,
    display_order   INTEGER NOT NULL DEFAULT 0,
    is_common_scale BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- prs_options (v6, unchanged)
-- Separate table for answer choices per question
-- TEXT PK: e.g. 'PDSS/004/03'
-- points: score contribution per answer choice
-- ============================================================
CREATE TABLE prs_options (
    option_id     TEXT PRIMARY KEY,
    question_id   TEXT NOT NULL REFERENCES prs_questions(question_id) ON DELETE CASCADE,
    option_label  TEXT NOT NULL,
    option_value  TEXT NOT NULL,
    points        NUMERIC NOT NULL DEFAULT 0,
    display_order INTEGER NOT NULL DEFAULT 0,
    status        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (question_id, option_value)
);


-- ============================================================
-- prs_scale_question_map (v6, unchanged)
-- Ordered list of questions within each scale
-- TEXT PK: e.g. 'PDSS/2026/PDSS/004'
-- ============================================================
CREATE TABLE prs_scale_question_map (
    sq_map_id     TEXT PRIMARY KEY,
    scale_id      TEXT NOT NULL REFERENCES prs_scales(scale_id) ON DELETE CASCADE,
    question_id   TEXT NOT NULL REFERENCES prs_questions(question_id) ON DELETE CASCADE,
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (scale_id, question_id)
);


-- ============================================================
-- prs_disease_question_map (v6, unchanged)
-- Flat denormalised map: every question reachable from a disease
-- Used by scoring engine for disease-level aggregation
-- TEXT PK: e.g. 'CHRONICPAIN/2026/DASS-21/006'
-- ============================================================
CREATE TABLE prs_disease_question_map (
    dq_map_id     TEXT PRIMARY KEY,
    disease_id    TEXT NOT NULL REFERENCES prs_diseases(disease_id) ON DELETE CASCADE,
    question_id   TEXT NOT NULL REFERENCES prs_questions(question_id) ON DELETE CASCADE,
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (disease_id, question_id)
);


-- ============================================================
-- prs_assessment_instances (v6 + Anava additions)
-- TEXT PK: e.g. 'PAT001/001'
--
-- Anava changes:
--   patient_id FK → profiles(id)  [was patients(id) in Supabase]
--   visit_id   → renamed session_id, FK → sessions(session_id)
--   + assessment_stage TEXT (general_registration/main_clinical/followup)
--   + cycle_id  UUID → treatment_cycles(cycle_id)  [NULL at registration]
--   + administered_by UUID → profiles(id)  [CA or Receptionist]
-- ============================================================
CREATE TABLE prs_assessment_instances (
    instance_id      TEXT PRIMARY KEY,
    disease_id       TEXT NOT NULL REFERENCES prs_diseases(disease_id) ON DELETE CASCADE,
    patient_id       UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    -- renamed from visit_id; NULL for general_registration (no session yet)
    session_id       UUID REFERENCES sessions(session_id) ON DELETE SET NULL,
    -- Anava: NULL for general_registration (no block yet)
    cycle_id         UUID REFERENCES treatment_cycles(cycle_id) ON DELETE SET NULL,
    initiated_by     assessment_taken_by NOT NULL DEFAULT 'patient',
    -- Anava: CA, Receptionist, or System who administered this instance
    administered_by  UUID REFERENCES profiles(id) ON DELETE SET NULL,
    -- Anava: which clinical stage this assessment belongs to.
    -- Default 'general_registration': safest default; app sets explicitly for other stages.
    assessment_stage TEXT NOT NULL DEFAULT 'general_registration'
                         CHECK (assessment_stage IN (
                             'general_registration',
                             'main_clinical',
                             'followup'
                         )),
    status           TEXT NOT NULL DEFAULT 'in_progress'
                         CHECK (status IN ('in_progress', 'completed', 'abandoned')),
    started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ,
    final_result     TEXT,  -- FK set later (deferred): prs_final_results.final_result_id
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- prs_responses (v6, unchanged)
-- One row per question per assessment instance
-- TEXT PK: e.g. 'PAT001/001/0006'
-- given_response: raw text answer
-- response_value: numeric score extracted from answer
-- UNIQUE(instance_id, question_id): one answer per question
-- ============================================================
CREATE TABLE prs_responses (
    response_id    TEXT PRIMARY KEY,
    instance_id    TEXT NOT NULL REFERENCES prs_assessment_instances(instance_id) ON DELETE CASCADE,
    question_id    TEXT NOT NULL REFERENCES prs_questions(question_id) ON DELETE CASCADE,
    given_response TEXT,
    response_value NUMERIC,
    time_stamp     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (instance_id, question_id)
);


-- ============================================================
-- prs_scale_results (v6, unchanged)
-- Computed score per scale within an assessment instance
-- percentage is GENERATED ALWAYS AS (calculated_value / max_possible * 100)
-- subscale_scores JSONB: breakdown by subscale where applicable
-- risk_flags JSONB: array of clinical risk indicators
-- TEXT PK: e.g. 'PAT001/001/EQ-5D-5L/2026'
-- ============================================================
CREATE TABLE prs_scale_results (
    scale_result_id  TEXT PRIMARY KEY,
    instance_id      TEXT NOT NULL REFERENCES prs_assessment_instances(instance_id) ON DELETE CASCADE,
    scale_id         TEXT NOT NULL REFERENCES prs_scales(scale_id) ON DELETE CASCADE,
    calculated_value NUMERIC,
    max_possible     NUMERIC,
    percentage       NUMERIC GENERATED ALWAYS AS (
                         CASE WHEN max_possible > 0
                              THEN ROUND((calculated_value / max_possible) * 100, 2)
                              ELSE NULL
                         END
                     ) STORED,
    severity_level   TEXT,
    severity_label   TEXT,
    subscale_scores  JSONB NOT NULL DEFAULT '{}',
    risk_flags       JSONB NOT NULL DEFAULT '[]',
    raw_score_data   JSONB NOT NULL DEFAULT '{}',
    time_stamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (instance_id, scale_id)
);


-- ============================================================
-- prs_final_results (v6, unchanged)
-- Aggregated score across ALL scales in one assessment instance
-- Upserted by trigger recalculate_final_result (see below)
-- TEXT PK: e.g. 'PAT001/001/CHRONICPAIN/2026'
-- ============================================================
CREATE TABLE prs_final_results (
    final_result_id        TEXT PRIMARY KEY,
    instance_id            TEXT NOT NULL UNIQUE
                               REFERENCES prs_assessment_instances(instance_id) ON DELETE CASCADE,
    calculated_value       NUMERIC,
    max_possible           NUMERIC,
    percentage             NUMERIC GENERATED ALWAYS AS (
                               CASE WHEN max_possible > 0
                                    THEN ROUND((calculated_value / max_possible) * 100, 2)
                                    ELSE NULL
                               END
                           ) STORED,
    scales_completed       INTEGER NOT NULL DEFAULT 0,
    scales_total           INTEGER NOT NULL DEFAULT 0,
    overall_severity       TEXT,
    overall_severity_label TEXT,
    scale_summaries        JSONB NOT NULL DEFAULT '[]',
    all_risk_flags         JSONB NOT NULL DEFAULT '[]',
    composite_summary      TEXT,
    time_stamp             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Deferred FK: instances ↔ final_results (circular dependency)
-- final_result on instances is set AFTER final_results row exists
ALTER TABLE prs_assessment_instances
    ADD CONSTRAINT fk_instance_final_result
    FOREIGN KEY (final_result)
    REFERENCES prs_final_results(final_result_id)
    ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED;


-- ============================================================
-- patient_scale_assignments (NEW — Anava only)
-- Tracks which specific PRS scales are assigned to each patient
-- Enables consistent longitudinal comparison across follow-ups
-- scale_id is TEXT FK (v6 TEXT PK on prs_scales)
-- ============================================================
CREATE TABLE patient_scale_assignments (
    psa_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id        UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    scale_id          TEXT NOT NULL REFERENCES prs_scales(scale_id) ON DELETE RESTRICT,
    assessment_stage  TEXT NOT NULL CHECK (assessment_stage IN (
                          'general_registration', 'main_clinical', 'followup'
                      )),
    assigned_by       UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    assignment_reason TEXT CHECK (assignment_reason IN (
                          'auto_disease_match', 'ca_selected', 'doctor_override'
                      )),
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    deactivated_at    TIMESTAMPTZ,
    deactivated_by    UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- TRIGGER: recalculate_final_result (v6, unchanged)
-- Fires AFTER INSERT OR UPDATE on prs_scale_results.
-- Aggregates all scale results for an instance into prs_final_results.
-- Marks instance as 'completed' when all scales are done.
-- ============================================================

CREATE OR REPLACE FUNCTION recalculate_final_result()
RETURNS TRIGGER AS $$
DECLARE
    v_instance      prs_assessment_instances%ROWTYPE;
    v_total         NUMERIC := 0;
    v_max           NUMERIC := 0;
    v_completed     INTEGER := 0;
    v_total_scales  INTEGER := 0;
    v_worst_sev     TEXT    := NULL;
    v_worst_label   TEXT    := NULL;
    v_summaries     JSONB   := '[]'::JSONB;
    v_all_flags     JSONB   := '[]'::JSONB;
    sev_order       INTEGER;
    worst_order     INTEGER := -1;
    r               RECORD;
BEGIN
    SELECT * INTO v_instance
    FROM prs_assessment_instances
    WHERE instance_id = NEW.instance_id;

    -- Count total scales for this disease
    SELECT COUNT(*) INTO v_total_scales
    FROM prs_disease_scale_map
    WHERE disease_id = v_instance.disease_id;

    -- Aggregate all scale results for this instance
    FOR r IN
        SELECT sr.*, sc.scale_code, sc.scale_name
        FROM prs_scale_results sr
        JOIN prs_scales sc ON sc.scale_id = sr.scale_id
        WHERE sr.instance_id = NEW.instance_id
    LOOP
        v_total     := v_total + COALESCE(r.calculated_value, 0);
        v_max       := v_max   + COALESCE(r.max_possible, 0);
        v_completed := v_completed + 1;

        -- Track worst severity across all scales
        sev_order := CASE r.severity_level
            WHEN 'severe'            THEN 4
            WHEN 'moderately-severe' THEN 3
            WHEN 'moderate'          THEN 2
            WHEN 'mild'              THEN 1
            ELSE 0
        END;
        IF sev_order > worst_order THEN
            worst_order   := sev_order;
            v_worst_sev   := r.severity_level;
            v_worst_label := r.severity_label;
        END IF;

        -- Build per-scale summary snapshot
        v_summaries := v_summaries || jsonb_build_object(
            'scale_code',     r.scale_code,
            'scale_name',     r.scale_name,
            'score',          r.calculated_value,
            'max_possible',   r.max_possible,
            'percentage',     CASE WHEN r.max_possible > 0
                                   THEN ROUND((r.calculated_value / r.max_possible) * 100, 2)
                                   ELSE NULL END,
            'severity_level', r.severity_level,
            'severity_label', r.severity_label
        );

        -- Collect risk flags from all scales
        IF r.risk_flags IS NOT NULL AND jsonb_array_length(r.risk_flags) > 0 THEN
            v_all_flags := v_all_flags || r.risk_flags;
        END IF;
    END LOOP;

    -- Upsert prs_final_results
    INSERT INTO prs_final_results (
        final_result_id,
        instance_id,
        calculated_value,
        max_possible,
        scales_completed,
        scales_total,
        overall_severity,
        overall_severity_label,
        scale_summaries,
        all_risk_flags,
        time_stamp
    ) VALUES (
        NEW.instance_id || '/' || v_instance.disease_id,
        NEW.instance_id,
        v_total,
        v_max,
        v_completed,
        v_total_scales,
        v_worst_sev,
        v_worst_label,
        v_summaries,
        v_all_flags,
        NOW()
    )
    ON CONFLICT (instance_id) DO UPDATE SET
        calculated_value        = EXCLUDED.calculated_value,
        max_possible            = EXCLUDED.max_possible,
        scales_completed        = EXCLUDED.scales_completed,
        scales_total            = EXCLUDED.scales_total,
        overall_severity        = EXCLUDED.overall_severity,
        overall_severity_label  = EXCLUDED.overall_severity_label,
        scale_summaries         = EXCLUDED.scale_summaries,
        all_risk_flags          = EXCLUDED.all_risk_flags,
        time_stamp              = EXCLUDED.time_stamp;

    -- Mark instance completed when all scales are scored
    IF v_completed >= v_total_scales THEN
        UPDATE prs_assessment_instances
        SET
            status       = 'completed',
            completed_at = NOW(),
            final_result = (
                SELECT final_result_id
                FROM prs_final_results
                WHERE instance_id = NEW.instance_id
            )
        WHERE instance_id = NEW.instance_id
          AND status != 'completed';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- DEFERRABLE INITIALLY DEFERRED: trigger fires at end-of-transaction, not per statement.
-- When the application submits all scale results for an instance in one transaction,
-- all N trigger calls fire at commit time. Each call is idempotent (ON CONFLICT DO UPDATE),
-- so the final aggregate is correct regardless of firing order.
-- Performance: O(N²) work but confined to commit phase; concurrent transactions unblocked.
CREATE CONSTRAINT TRIGGER trg_recalculate_final_result
    AFTER INSERT OR UPDATE ON prs_scale_results
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION recalculate_final_result();
