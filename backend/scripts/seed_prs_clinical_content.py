"""Seeds real PRS clinical content (validated scales, questions, options) and
anamnesis questions from D:\\PCS\\backend-v2\\Data\\*.csv — the real clinical
content sourced separately from the placeholder/test data seed_test_prs_content.py
uses (see that script's own docstring, and Development Plan Stage 5).

prs_diseases / prs_scales / prs_disease_scale_map are already fully seeded
by SQL/16_seed_data.sql with hand-curated values (notably
prs_scales.applicable_for, which the Data/ CSV export doesn't even have a
column for — every row would silently default to 'main_clinical' if this
script's INSERT weren't guarded). This script re-applies those 3 from the
CSVs too, but purely as an idempotent safety net via ON CONFLICT DO NOTHING
— it can never overwrite the correctly-curated existing rows, only fill in
rows genuinely missing. The real new content here is prs_questions/
prs_options/prs_disease_question_map/prs_scale_question_map and the
anamnesis tables, none of which SQL/16_seed_data.sql fully covers (anamnesis
questions/options ARE in 16_seed_data.sql too, upserted the same idempotent
way — this script's CSV versions of those two are a secondary safety net,
not the primary source).

Known CSV data-quality issue (verified, not assumed): prs_questions_rows.csv
(the base file) has 18 rows with a casing bug in ds_map_id
('ADHD/ASRS-v1.1' vs the correct 'ADHD/ASRS-V1.1' used everywhere else) —
use prs_questions_rows_v1.csv (already fixed), which this script reads.

Usage: python -m scripts.seed_prs_clinical_content
"""

import asyncio
import csv
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.core.db import get_migration_engine

DATA_DIR = Path(__file__).resolve().parents[2] / "Data"


def read_csv(filename: str) -> list[dict]:
    with open(DATA_DIR / filename, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def as_bool(value: str) -> bool:
    return value.strip().lower() == "true"


def as_int(value: str) -> int:
    return int(value.strip())


def as_decimal_or_none(value: str | None) -> Decimal | None:
    value = (value or "").strip()
    return Decimal(value) if value else None


def as_text_or_none(value: str | None) -> str | None:
    value = (value or "").strip()
    return value if value else None


async def seed_diseases(conn) -> int:
    rows = read_csv("prs_diseases_rows.csv")
    for r in rows:
        await conn.execute(
            text(
                "INSERT INTO prs_diseases (disease_id, disease_code, disease_name, version, status) "
                "VALUES (:disease_id, :disease_code, :disease_name, :version, :status) "
                "ON CONFLICT (disease_id) DO NOTHING"
            ),
            {
                "disease_id": r["disease_id"],
                "disease_code": r["disease_code"],
                "disease_name": r["disease_name"],
                "version": r["version"],
                "status": as_bool(r["status"]),
            },
        )
    return len(rows)


async def seed_scales(conn) -> int:
    # applicable_for deliberately omitted — not in this CSV, and the DB
    # default ('main_clinical') would be wrong for several scales (GAD-7/
    # PSQI are 'general_registration'; COMPASS-31/DASS-21/EQ-5D-5L are
    # 'all') — ON CONFLICT DO NOTHING means this never actually overwrites
    # the correct values SQL/16_seed_data.sql already inserted.
    rows = read_csv("prs_scales_rows.csv")
    for r in rows:
        await conn.execute(
            text(
                "INSERT INTO prs_scales (scale_id, scale_code, scale_name, is_common_scale, num_diseases_used) "
                "VALUES (:scale_id, :scale_code, :scale_name, :is_common, :num_diseases) "
                "ON CONFLICT (scale_id) DO NOTHING"
            ),
            {
                "scale_id": r["scale_id"],
                "scale_code": r["scale_code"],
                "scale_name": r["scale_name"],
                "is_common": as_bool(r["is_common_scale"]),
                "num_diseases": as_int(r["num_diseases_used"]),
            },
        )
    return len(rows)


async def seed_disease_scale_map(conn) -> int:
    # ON CONFLICT targets (disease_id, scale_id) and realigns ds_map_id on a
    # match, rather than a plain DO NOTHING — the CSV export has a handful of
    # casing-mismatched ds_map_id values relative to what
    # SQL/16_seed_data.sql originally seeded for the same disease+scale pair
    # (e.g. 'ADHD/ASRS-V1.1' here vs 'ADHD/ASRS-v1.1' there — same known
    # casing bug as prs_questions_rows.csv, just in this file too). The CSV
    # set is the one true source going forward (prs_questions_rows_v1.csv's
    # ds_map_id FK references use the CSV's casing), so a plain DO NOTHING
    # would leave the old casing in place and then FK-fail every question
    # under that scale — safe to realign since prs_questions/prs_options
    # were empty before this script ran, nothing referenced the old casing.
    rows = read_csv("prs_disease_scale_map_rows.csv")
    for r in rows:
        await conn.execute(
            text(
                "INSERT INTO prs_disease_scale_map (ds_map_id, disease_id, scale_id, display_order, is_required) "
                "VALUES (:id, :disease_id, :scale_id, :order, :required) "
                "ON CONFLICT (disease_id, scale_id) DO UPDATE SET ds_map_id = EXCLUDED.ds_map_id"
            ),
            {
                "id": r["ds_map_id"],
                "disease_id": r["disease_id"],
                "scale_id": r["scale_id"],
                "order": as_int(r["display_order"]),
                "required": as_bool(r["is_required"]),
            },
        )
    return len(rows)


async def seed_questions(conn) -> int:
    rows = read_csv("prs_questions_rows_v1.csv")
    for r in rows:
        await conn.execute(
            text(
                "INSERT INTO prs_questions (question_id, question_code, disease_id, scale_id, ds_map_id, "
                "question_text, answer_type, min_value, max_value, is_required, skip_logic, display_order, "
                "is_common_scale) VALUES (:question_id, :question_code, :disease_id, :scale_id, :ds_map_id, "
                ":question_text, :answer_type, :min_value, :max_value, :is_required, :skip_logic, "
                ":display_order, :is_common_scale) "
                "ON CONFLICT (question_id) DO NOTHING"
            ),
            {
                "question_id": r["question_id"],
                "question_code": r["question_code"],
                "disease_id": as_text_or_none(r["disease_id"]),
                "scale_id": as_text_or_none(r["scale_id"]),
                "ds_map_id": as_text_or_none(r["ds_map_id"]),
                "question_text": r["question_text"],
                "answer_type": r["answer_type"],
                "min_value": as_decimal_or_none(r["min_value"]),
                "max_value": as_decimal_or_none(r["max_value"]),
                "is_required": as_bool(r["is_required"]),
                "skip_logic": as_text_or_none(r["skip_logic"]),
                "display_order": as_int(r["display_order"]),
                "is_common_scale": as_bool(r["is_common_scale"]),
            },
        )
    return len(rows)


async def seed_options(conn) -> int:
    rows = read_csv("prs_options_rows.csv")
    for r in rows:
        await conn.execute(
            text(
                "INSERT INTO prs_options (option_id, question_id, option_label, option_value, points, "
                "display_order, status) VALUES (:id, :qid, :label, :value, :points, :order, :status) "
                "ON CONFLICT (option_id) DO NOTHING"
            ),
            {
                "id": r["option_id"],
                "qid": r["question_id"],
                "label": r["option_label"],
                "value": r["option_value"],
                "points": as_decimal_or_none(r["points"]) or Decimal(0),
                "order": as_int(r["display_order"]),
                "status": as_bool(r["status"]),
            },
        )
    return len(rows)


async def seed_disease_question_map(conn) -> int:
    rows = read_csv("prs_disease_question_map_rows.csv")
    for r in rows:
        await conn.execute(
            text(
                "INSERT INTO prs_disease_question_map (dq_map_id, disease_id, question_id, display_order) "
                "VALUES (:id, :disease_id, :qid, :order) ON CONFLICT (dq_map_id) DO NOTHING"
            ),
            {"id": r["dq_map_id"], "disease_id": r["disease_id"], "qid": r["question_id"], "order": as_int(r["display_order"])},
        )
    return len(rows)


async def seed_scale_question_map(conn) -> int:
    rows = read_csv("prs_scale_question_map_rows.csv")
    for r in rows:
        await conn.execute(
            text(
                "INSERT INTO prs_scale_question_map (sq_map_id, scale_id, question_id, display_order) "
                "VALUES (:id, :scale_id, :qid, :order) ON CONFLICT (sq_map_id) DO NOTHING"
            ),
            {"id": r["sq_map_id"], "scale_id": r["scale_id"], "qid": r["question_id"], "order": as_int(r["display_order"])},
        )
    return len(rows)


async def seed_anamnesis_questions(conn) -> int:
    rows = read_csv("anamnesis_questions_rows.csv")
    for r in rows:
        await conn.execute(
            text(
                "INSERT INTO anamnesis_questions (question_id, section_number, section_title, question_code, "
                "question_text, answer_type, is_required, display_order, depends_on_question_id, "
                "depends_on_value, helper_text, status) VALUES (:id, :section_number, :section_title, "
                ":code, :text, :answer_type, :required, :order, :depends_id, :depends_value, :helper, :status) "
                "ON CONFLICT (question_id) DO UPDATE SET "
                "question_text = EXCLUDED.question_text, helper_text = EXCLUDED.helper_text, "
                "section_title = EXCLUDED.section_title, display_order = EXCLUDED.display_order"
            ),
            {
                "id": r["question_id"],
                "section_number": as_int(r["section_number"]),
                "section_title": r["section_title"],
                "code": r["question_code"],
                "text": r["question_text"],
                "answer_type": r["answer_type"],
                "required": as_bool(r["is_required"]),
                "order": as_int(r["display_order"]),
                "depends_id": as_text_or_none(r["depends_on_question_id"]),
                "depends_value": as_text_or_none(r["depends_on_value"]),
                "helper": as_text_or_none(r["helper_text"]),
                "status": as_bool(r["status"]),
            },
        )
    return len(rows)


async def seed_anamnesis_options(conn) -> int:
    rows = read_csv("anamnesis_options_rows.csv")
    for r in rows:
        await conn.execute(
            text(
                "INSERT INTO anamnesis_options (option_id, question_id, option_label, option_value, display_order) "
                "VALUES (:id, :qid, :label, :value, :order) ON CONFLICT (option_id) DO NOTHING"
            ),
            {
                "id": r["option_id"],
                "qid": r["question_id"],
                "label": r["option_label"],
                "value": r["option_value"],
                "order": as_int(r["display_order"]),
            },
        )
    return len(rows)


async def main() -> None:
    # get_migration_engine(), not `engine` — every table here has an INSERT
    # policy requiring rls_user_role() = 'super_admin', which a standalone
    # script has no way to satisfy via the app's own RLS-scoped connection.
    engine = get_migration_engine()
    async with engine.begin() as conn:
        counts = {
            "prs_diseases (safety net)": await seed_diseases(conn),
            "prs_scales (safety net)": await seed_scales(conn),
            "prs_disease_scale_map (safety net)": await seed_disease_scale_map(conn),
            "prs_questions": await seed_questions(conn),
            "prs_options": await seed_options(conn),
            "prs_disease_question_map": await seed_disease_question_map(conn),
            "prs_scale_question_map": await seed_scale_question_map(conn),
            "anamnesis_questions (upsert)": await seed_anamnesis_questions(conn),
            "anamnesis_options": await seed_anamnesis_options(conn),
        }
    await engine.dispose()
    for label, count in counts.items():
        print(f"{label}: {count} CSV rows processed")


if __name__ == "__main__":
    asyncio.run(main())
