-- ============================================================
-- Anava Clinic — DB Schema
-- File 08: Anamnesis Tables
--
-- BASE RULE: Keep v6 4-table structure exactly.
-- Application code depends on this structure.
--
-- CHANGES FROM v6:
--   anamnesis_assessments:
--     + ADD cycle_id UUID  (NULL at registration, set on block-level updates)
--     + ADD version INTEGER (increments on each update to this patient's anamnesis)
--     ~ patient_id FK → profiles(id)  [was profiles(id) in v6 too — no change]
--     ~ removed UNIQUE(patient_id) — Anava allows multiple versions per patient
--       (one per block update); uniqueness enforced at application layer
--   anamnesis_questions, anamnesis_options, anamnesis_responses — UNCHANGED
--
-- SEED DATA (questions + options):
--   Questions seeded separately via admin portal or seed script.
--   anamnesis_questions contains all form questions (section-based).
-- ============================================================


-- ============================================================
-- TABLE 1: anamnesis_assessments
-- Header record — one per assessment event (registration + each update)
-- v6: TEXT PK "ANA/{patient_id[:8]}/NNN"
-- Anava adds: cycle_id, version
-- ============================================================
CREATE TABLE anamnesis_assessments (
    anamnesis_id TEXT PRIMARY KEY,
    -- composite format: "ANA/{patient_id_first8}/{version_padded3}"
    -- e.g. "ANA/a1b2c3d4/001" at registration, "ANA/a1b2c3d4/002" on first update
    patient_id   UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    submitted_by UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    -- assessment_taken_by ENUM defined in 07_prs_tables.sql
    taken_by     assessment_taken_by NOT NULL DEFAULT 'patient',
    -- NULL at registration (no block exists yet)
    cycle_id     UUID REFERENCES treatment_cycles(cycle_id) ON DELETE RESTRICT,
    -- 1 at registration, increments on each update
    version      INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    status       TEXT NOT NULL DEFAULT 'in_progress'
                     CHECK (status IN ('in_progress', 'completed')),
    completed_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Prevent duplicate versions for same patient
    UNIQUE (patient_id, version)
);


-- ============================================================
-- TABLE 2: anamnesis_questions
-- Seed/reference table — defines every question in the form.
-- Read-only for all clinical users. Admin/Super Admin write.
-- TEXT PK: "ANA/S02/Q003"  (section/question composite)
-- depends_on_question_id: conditional display logic
-- ============================================================
CREATE TABLE anamnesis_questions (
    question_id            TEXT PRIMARY KEY,
    section_number         INTEGER NOT NULL,
    section_title          TEXT NOT NULL,
    question_code          TEXT NOT NULL UNIQUE,  -- snake_case, used by API
    question_text          TEXT NOT NULL,
    answer_type            TEXT NOT NULL CHECK (answer_type IN (
                               'text', 'textarea', 'radio',
                               'select', 'checkbox', 'conditional_text'
                           )),
    is_required            BOOLEAN NOT NULL DEFAULT TRUE,
    display_order          INTEGER NOT NULL DEFAULT 0,
    depends_on_question_id TEXT REFERENCES anamnesis_questions(question_id),
    depends_on_value       TEXT,
    helper_text            TEXT,
    status                 BOOLEAN NOT NULL DEFAULT TRUE
);


-- ============================================================
-- TABLE 3: anamnesis_options
-- One row per selectable choice for radio/select/checkbox questions.
-- text/textarea/conditional_text questions have no rows here.
-- TEXT PK: "ANA/S02/Q003/O01"
-- ============================================================
CREATE TABLE anamnesis_options (
    option_id     TEXT PRIMARY KEY,
    question_id   TEXT NOT NULL REFERENCES anamnesis_questions(question_id) ON DELETE CASCADE,
    option_label  TEXT NOT NULL,
    option_value  TEXT NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE (question_id, option_value)
);


-- ============================================================
-- TABLE 4: anamnesis_responses
-- One row per question per assessment instance.
-- Upserted as patient fills form; locked after status=completed.
-- TEXT PK: "{anamnesis_id}|{question_id}"
-- response_value:  text/radio/select/conditional_text answers
-- response_values: TEXT[] for checkbox multi-select answers
-- ============================================================
CREATE TABLE anamnesis_responses (
    response_id     TEXT PRIMARY KEY,
    -- composite: "{anamnesis_id}|{question_id}"
    -- e.g. "ANA/a1b2c3d4/001|ANA/S02/Q007"
    anamnesis_id    TEXT NOT NULL REFERENCES anamnesis_assessments(anamnesis_id) ON DELETE CASCADE,
    question_id     TEXT NOT NULL REFERENCES anamnesis_questions(question_id),
    response_value  TEXT,
    response_values TEXT[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- Note: TEXT PK "{anamnesis_id}|{question_id}" already enforces uniqueness.
    -- A separate UNIQUE (anamnesis_id, question_id) would create a redundant second index.
);
