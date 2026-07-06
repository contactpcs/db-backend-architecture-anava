"""Dev-only: prs_questions has ZERO rows in the real schema — actual clinical
scale content (PRS_DET.xlsx -> seed_scales.py) is being sourced separately
(user-owned, see Development Plan Stage 5 blocker note). This seeds just 2
real GAD-7 items (a standard, public-domain screening scale) against the
already-seeded GAD-7/2026 scale, so the PRS module's scoring flow is
actually testable end-to-end. Not the full clinical battery — swap for real
content when seed_scales.py exists, this script's rows will need clearing.

Usage: python -m scripts.seed_test_prs_content
"""

import asyncio

from sqlalchemy import text

from app.core.db import get_migration_engine

QUESTIONS = [
    ("GAD-7/001", "GAD-7/2026", "Feeling nervous, anxious, or on edge", "GAD7Q1"),
    ("GAD-7/002", "GAD-7/2026", "Not being able to stop or control worrying", "GAD7Q2"),
]
OPTIONS = [
    ("Not at all", "0", 0),
    ("Several days", "1", 1),
    ("More than half the days", "2", 2),
    ("Nearly every day", "3", 3),
]


async def main() -> None:
    # get_migration_engine(), not `engine` — rls_prs_questions_write/
    # rls_prs_opts_write both require rls_user_role() = 'super_admin', which
    # a standalone script (no HTTP request context) can never satisfy via
    # the app's own RLS-scoped connection. See core/db.py::get_migration_engine.
    engine = get_migration_engine()
    async with engine.begin() as conn:
        for order, (qid, scale_id, text_, code) in enumerate(QUESTIONS):
            await conn.execute(
                text(
                    "INSERT INTO prs_questions (question_id, question_code, scale_id, question_text, "
                    "answer_type, display_order) VALUES (:qid, :code, :scale_id, :text_, 'likert', :order) "
                    "ON CONFLICT (question_id) DO NOTHING"
                ),
                {"qid": qid, "code": code, "scale_id": scale_id, "text_": text_, "order": order},
            )
            for opt_order, (label, value, points) in enumerate(OPTIONS):
                option_id = f"{qid}/{opt_order:02d}"
                await conn.execute(
                    text(
                        "INSERT INTO prs_options (option_id, question_id, option_label, option_value, "
                        "points, display_order) VALUES (:oid, :qid, :label, :value, :points, :order) "
                        "ON CONFLICT (option_id) DO NOTHING"
                    ),
                    {"oid": option_id, "qid": qid, "label": label, "value": value, "points": points, "order": opt_order},
                )
            sq_map_id = f"GAD-7/2026/{qid}"
            await conn.execute(
                text(
                    "INSERT INTO prs_scale_question_map (sq_map_id, scale_id, question_id, display_order) "
                    "VALUES (:id, :scale_id, :qid, :order) ON CONFLICT (sq_map_id) DO NOTHING"
                ),
                {"id": sq_map_id, "scale_id": scale_id, "qid": qid, "order": order},
            )
    await engine.dispose()
    print(f"Seeded {len(QUESTIONS)} test GAD-7 questions with options + scale mapping.")


if __name__ == "__main__":
    asyncio.run(main())
