"""SQL/v1/123 against the real schema: the full form is part of the template
hash, frozen on publish, and survives a re-run of 122; a signed record's
full-form evidence can never change."""

import hashlib
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy.exc import DBAPIError

from tests.integration.conftest import needs_test_database

pytestmark = needs_test_database

SQL_DIR = Path(__file__).resolve().parents[3] / "SQL" / "v1"


@pytest.fixture
async def draft(admin):
    tid = str(uuid.uuid4())
    await admin(
        "INSERT INTO consent_templates (template_id, consent_type, language, version, title, content, clauses, full_html, is_active) "
        "VALUES (:id, 'patient_clinic_exit', 'xx', :v, 'T', 'Summary', '[]'::jsonb, '<p>[PATIENT_NAME]</p>', FALSE)",
        id=tid,
        v=uuid.uuid4().int % 1_000_000 + 1000,  # published leftovers must not collide
    )
    yield tid
    await admin("DELETE FROM consent_templates WHERE template_id = :id AND published_at IS NULL", id=tid)


async def _row(admin, tid):
    return (
        (
            await admin(
                "SELECT title, content, clauses::text AS clauses, full_html, content_hash FROM consent_templates WHERE template_id = :id",
                id=tid,
            )
        )
        .mappings()
        .one()
    )


def _expected(row) -> str:
    text = f"{row['title']}\n{row['content']}\n{row['clauses']}" + (f"\n{row['full_html']}" if row["full_html"] is not None else "")
    return hashlib.sha256(text.encode()).hexdigest()


async def test_hash_covers_the_full_form_and_matches_122_without_it(admin, draft):
    row = await _row(admin, draft)
    assert row["content_hash"] == _expected(row)
    await admin("UPDATE consent_templates SET full_html = '<p>changed</p>' WHERE template_id = :id", id=draft)
    changed = await _row(admin, draft)
    assert changed["content_hash"] != row["content_hash"] and changed["content_hash"] == _expected(changed)
    await admin("UPDATE consent_templates SET full_html = NULL WHERE template_id = :id", id=draft)
    assert (await _row(admin, draft))["content_hash"] == _expected(await _row(admin, draft))


async def test_published_full_form_is_frozen(admin, draft):
    await admin("UPDATE consent_templates SET published_at = now() WHERE template_id = :id", id=draft)
    before = await _row(admin, draft)
    with pytest.raises(DBAPIError, match="is published"):
        await admin("UPDATE consent_templates SET full_html = '<p>sneaky edit</p>' WHERE template_id = :id", id=draft)
    with pytest.raises(DBAPIError, match="is published"):
        await admin("UPDATE consent_templates SET full_html = NULL WHERE template_id = :id", id=draft)
    # Retiring a published version is still allowed and keeps its hash.
    await admin("UPDATE consent_templates SET is_active = FALSE WHERE template_id = :id", id=draft)
    assert (await _row(admin, draft))["content_hash"] == before["content_hash"]


async def test_oversized_full_form_is_refused_by_the_db(admin, draft):
    with pytest.raises(DBAPIError, match="chk_consent_templates_full_html_size"):
        await admin("UPDATE consent_templates SET full_html = repeat('x', 1048577) WHERE template_id = :id", id=draft)


def _statements(sql_file: str) -> list[str]:
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0069_consent_full_html.py"
    spec = importlib.util.spec_from_file_location("m0069", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module.split_statements((SQL_DIR / sql_file).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "order", [("122_consent_v2.sql", "123_consent_full_html.sql"), ("123_consent_full_html.sql", "122_consent_v2.sql")]
)
async def test_rerunning_122_and_123_in_any_order_keeps_both_guards(admin, draft, order):
    """The SQL 97-after-74 trap: a re-run of the older file must not undo the newer one."""
    from app.core.db import get_migration_engine

    engine = get_migration_engine()
    async with engine.begin() as conn:  # one transaction: 122 uses ON COMMIT DROP temp tables
        for name in order:
            for statement in _statements(name):
                await conn.exec_driver_sql(statement)
    await engine.dispose()
    row = await _row(admin, draft)
    assert row["content_hash"] == _expected(row)  # full_html still hashed
    await admin("UPDATE consent_templates SET published_at = now() WHERE template_id = :id", id=draft)
    with pytest.raises(DBAPIError, match="is published"):
        await admin("UPDATE consent_templates SET full_html = '<p>x</p>' WHERE template_id = :id", id=draft)
    with pytest.raises(DBAPIError, match="is published"):
        await admin("UPDATE consent_templates SET content = 'x' WHERE template_id = :id", id=draft)  # 122's guard


async def test_signed_record_full_form_evidence_is_frozen(admin):
    ids = {k: str(uuid.uuid4()) for k in ("region", "clinic", "patient", "template", "consent")}
    await admin(
        "INSERT INTO regions (region_id, region_name, country, state) VALUES (:id, :n, 'IN', :n)",
        id=ids["region"],
        n=f"r-{ids['region'][:8]}",
    )
    await admin(
        "INSERT INTO clinics (clinic_id, clinic_code, clinic_name, clinic_type, region_id) VALUES (:id, :c, 'C', 'clinic', :r)",
        id=ids["clinic"],
        c=f"T{ids['clinic'][:8]}",
        r=ids["region"],
    )
    await admin(
        "INSERT INTO profiles (id, cognito_sub, email, first_name, last_name, role) VALUES (:id, :s, :e, 'A', 'B', 'patient')",
        id=ids["patient"],
        s=f"test-{ids['patient']}",
        e=f"{ids['patient']}@example.com",
    )
    await admin(
        "INSERT INTO consent_templates "
        "(template_id, consent_type, language, version, title, content, clauses, full_html, is_active, published_at) "
        "VALUES (:id, 'patient_clinic_exit', 'yy', :v, 'T', 'S', '[]'::jsonb, '<p>x</p>', FALSE, now())",
        id=ids["template"],
        v=uuid.uuid4().int % 1_000_000 + 1000,
    )
    await admin(
        "INSERT INTO consent_records (consent_id, consent_type, template_id, patient_id, clinic_id) "
        "VALUES (:id, 'patient_clinic_exit', :t, :p, :c)",
        id=ids["consent"],
        t=ids["template"],
        p=ids["patient"],
        c=ids["clinic"],
    )
    await admin("UPDATE consent_records SET full_viewed_at = now() WHERE consent_id = :id", id=ids["consent"])  # pending: allowed
    await admin(
        "UPDATE consent_records SET status = 'signed', signed_at = now(), fill_values = CAST(:fv AS JSONB), rendered_full_hash = 'h' "
        "WHERE consent_id = :id",
        id=ids["consent"],
        fv=json.dumps({"PATIENT_NAME": "A B"}),
    )
    for change in ("fill_values = '{}'::jsonb", "rendered_full_hash = 'other'", "full_viewed_at = now() + interval '1 day'"):
        with pytest.raises(DBAPIError, match="signed content cannot change"):
            await admin(f"UPDATE consent_records SET {change} WHERE consent_id = :id", id=ids["consent"])
    with pytest.raises(DBAPIError, match="chk_consent_records_fill_values_object"):
        await admin(
            "INSERT INTO consent_records (consent_type, template_id, patient_id, clinic_id, fill_values) "
            "VALUES ('patient_clinic_exit', :t, :p, :c, '[]'::jsonb)",
            t=ids["template"],
            p=ids["patient"],
            c=ids["clinic"],
        )
