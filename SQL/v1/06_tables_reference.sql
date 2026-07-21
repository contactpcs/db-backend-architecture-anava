-- Generated from live production schema introspection (2026-07-20). Do not hand-edit column/RLS/trigger/function bodies — regenerate from source instead.

CREATE TABLE reference."anamnesis_options" (
    "option_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "option_label" TEXT NOT NULL,
    "option_value" TEXT NOT NULL,
    "display_order" INTEGER NOT NULL DEFAULT 0
);


CREATE TABLE reference."anamnesis_questions" (
    "question_id" TEXT NOT NULL,
    "section_number" INTEGER NOT NULL,
    "section_title" TEXT NOT NULL,
    "question_code" TEXT NOT NULL,
    "question_text" TEXT NOT NULL,
    "answer_type" TEXT NOT NULL,
    "is_required" BOOLEAN NOT NULL DEFAULT true,
    "display_order" INTEGER NOT NULL DEFAULT 0,
    "depends_on_question_id" TEXT,
    "depends_on_value" TEXT,
    "helper_text" TEXT,
    "status" BOOLEAN NOT NULL DEFAULT true
);


CREATE TABLE reference."consent_templates" (
    "template_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "consent_type" TEXT NOT NULL,
    "version" INTEGER NOT NULL DEFAULT 1,
    "title" TEXT NOT NULL,
    "content" TEXT NOT NULL,
    "content_hash" TEXT,
    "effective_date" DATE,
    "expiry_date" DATE,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "role" TEXT
);


CREATE TABLE reference."products" (
    "product_id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "name" TEXT NOT NULL,
    "description" TEXT,
    "category" TEXT NOT NULL,
    "price" NUMERIC(10,2) NOT NULL,
    "sku" TEXT,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_disease_question_map" (
    "dq_map_id" TEXT NOT NULL,
    "disease_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "display_order" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_disease_scale_map" (
    "ds_map_id" TEXT NOT NULL,
    "disease_id" TEXT NOT NULL,
    "scale_id" TEXT NOT NULL,
    "display_order" INTEGER NOT NULL DEFAULT 0,
    "is_required" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_diseases" (
    "disease_id" TEXT NOT NULL,
    "disease_code" TEXT NOT NULL,
    "disease_name" TEXT NOT NULL,
    "version" TEXT NOT NULL DEFAULT 'v1.0'::text,
    "status" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_option_translations" (
    "option_id" TEXT NOT NULL,
    "language_code" VARCHAR(10) NOT NULL,
    "option_label" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_options" (
    "option_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "option_label" TEXT NOT NULL,
    "option_value" TEXT NOT NULL,
    "points" NUMERIC NOT NULL DEFAULT 0,
    "display_order" INTEGER NOT NULL DEFAULT 0,
    "status" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_question_translations" (
    "question_id" TEXT NOT NULL,
    "language_code" VARCHAR(10) NOT NULL,
    "question_text" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_questions" (
    "question_id" TEXT NOT NULL,
    "question_code" TEXT NOT NULL,
    "disease_id" TEXT,
    "scale_id" TEXT,
    "ds_map_id" TEXT,
    "question_text" TEXT NOT NULL,
    "answer_type" TEXT NOT NULL,
    "min_value" NUMERIC,
    "max_value" NUMERIC,
    "is_required" BOOLEAN NOT NULL DEFAULT true,
    "skip_logic" TEXT,
    "display_order" INTEGER NOT NULL DEFAULT 0,
    "is_common_scale" BOOLEAN NOT NULL DEFAULT false,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_scale_question_map" (
    "sq_map_id" TEXT NOT NULL,
    "scale_id" TEXT NOT NULL,
    "question_id" TEXT NOT NULL,
    "display_order" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE reference."prs_scales" (
    "scale_id" TEXT NOT NULL,
    "scale_code" TEXT NOT NULL,
    "scale_name" TEXT NOT NULL,
    "is_common_scale" BOOLEAN NOT NULL DEFAULT false,
    "num_diseases_used" INTEGER NOT NULL DEFAULT 1,
    "applicable_for" TEXT NOT NULL DEFAULT 'main_clinical'::text,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

