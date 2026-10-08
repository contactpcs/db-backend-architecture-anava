"""Builds a fresh database from SQL/v1/, file by file, in the order the schema
was developed. Alembic cannot do this from empty (revision 0001 points at an old
SQL folder that no longer exists, and it does not cover every file), so this is
the supported path for a new database. Afterwards `alembic stamp head` records
that the schema is current.

Reads MIGRATION_DATABASE_URL (the `postgres` user) and DATABASE_URL (to learn the
password to give the `anava_app` role) from backend/.env. Nothing is hardcoded.

    python -m scripts.apply_sql_v1                  # apply everything, then stamp alembic
    python -m scripts.apply_sql_v1 --reset --yes-drop-everything
        # DROP the whole database, recreate it empty, then apply everything
    python -m scripts.apply_sql_v1 --dry-run        # print the order, touch nothing
    python -m scripts.apply_sql_v1 --no-stamp       # skip `alembic stamp head`
    python -m scripts.apply_sql_v1 --assume-applied-through 17_rls_policies.sql
        # mark files up to and including that one as already applied (use it
        # when the first files were run by hand), then apply the rest

Resumable: each applied file is recorded in public._sql_v1_applied, so after a
failure fix the cause and run the same command again — it continues at the
file that failed. Stops at the first error.

Needs `psql` on PATH. Run it against an EMPTY database; files such as
34_drop_legacy_appointment_objects.sql are destructive by design.
"""

import argparse
import re
import secrets
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import get_settings

SQL_DIR = Path(__file__).resolve().parents[2] / "SQL" / "v1"
BACKEND_DIR = Path(__file__).resolve().parents[1]

# Where two files share a number, the order they were written in (taken from the
# alembic revision chain). Files not listed here sort alphabetically.
TIE_ORDER = {
    "45_anamnesis_assessment_stage.sql": 0,
    "45_protocol_instances.sql": 1,
    "54_custom_montage_protocol_link.sql": 0,
    "54_drop_device_slot_duration.sql": 1,
    "55_custom_montage_device_trigger_fix.sql": 0,
    "55_drop_device_schedule_capacity.sql": 1,
    "56_device_session_records.sql": 0,
    "56_audit_log_patient_role_rls.sql": 1,
    "97_device_catalogue_admin_writes.sql": 0,
    "97_fix_recalculate_final_result_disease_scope.sql": 1,
}

# 02_roles.sql expects `anava_app` to exist already (it did on the old cluster).
# It is created right after 02 so 18_grants.sql can grant to it.
CREATE_APP_ROLE_AFTER = "02_roles.sql"
# 02 also creates two roles with the placeholder password CHANGE_ME_BEFORE_USE.
# On a database reachable from the internet that is a known password, so they
# get random ones immediately.
RANDOMIZE_ROLES = ("anava_readonly", "anava_compliance")

# The clinical content (diseases, scales, questions, options) is not in SQL/v1: it
# is loaded from Data/*.csv by scripts/seed_prs_clinical_content.py. File 79 and
# later UPDATE and extend that content, so it has to exist first.
SEED_BEFORE = "79_psqi_full_instrument_seed.sql"
SEED_MODULE = "scripts.seed_prs_clinical_content"
SEED_KEY = "(python) scripts.seed_prs_clinical_content"

SEARCH_PATH = "core,reference,compliance,analytics,ops,extensions,public"


def sql_files() -> list[Path]:
    """00..110 in numeric order. Skips the helper files (leading underscore) and
    the *.run.sql copy-paste variants of 47-49, which duplicate the full files."""
    names = [
        p.name for p in SQL_DIR.glob("*.sql") if re.match(r"\d", p.name) and not p.name.startswith("_") and not p.name.endswith(".run.sql")
    ]

    def key(name: str):
        m = re.match(r"(\d+)(b?)", name)
        return (int(m.group(1)), m.group(2), TIE_ORDER.get(name, 0), name)

    return [SQL_DIR / n for n in sorted(names, key=key)]


def psql_env(url) -> dict:
    import os

    env = dict(os.environ)
    env["PGPASSWORD"] = url.password or ""
    env["PGSSLMODE"] = "require"
    # ALTER DATABASE ... SET search_path (19_search_path.sql) only reaches NEW
    # connections, but PL/pgSQL function bodies resolve table names when they are
    # created. Setting it on every connection from the start avoids
    # 'relation "..." does not exist' while creating functions.
    env["PGOPTIONS"] = f"-c search_path={SEARCH_PATH}"
    return env


def _can_run_in_one_transaction(file: Path) -> bool:
    """False for a file that manages its own transaction or uses a statement
    Postgres refuses inside one (CREATE INDEX CONCURRENTLY, VACUUM, ...)."""
    text = file.read_text()
    return not re.search(r"^\s*(BEGIN|COMMIT|ROLLBACK|VACUUM)\b|CONCURRENTLY", text, re.I | re.M)


def psql(url, env: dict, *, file: Path | None = None, command: str | None = None, capture: bool = False):
    cmd = [
        "psql",
        "-h",
        url.host,
        "-p",
        str(url.port or 5432),
        "-U",
        url.username,
        "-d",
        url.database,
        "-v",
        "ON_ERROR_STOP=1",
        "-q",
    ]
    if file and _can_run_in_one_transaction(file):
        # A file that fails or is interrupted halfway leaves nothing behind,
        # so running the script again is safe.
        cmd.append("--single-transaction")
    cmd += ["-f", str(file)] if file else ["-c", command]
    if capture:
        cmd += ["-t", "-A"]
    return subprocess.run(cmd, env=env, capture_output=capture, text=True)


def reset_database(admin, env: dict) -> None:
    """Drops and recreates the database, connecting to the maintenance database
    `postgres` to do it (a database cannot be dropped from inside itself).
    Roles live at the cluster level and are kept; the role setup below is
    idempotent. The database-level search_path setting goes with the database
    and is set again by 19_search_path.sql."""
    name = admin.database
    maint = admin.set(database="postgres")
    print(f"Dropping database {name!r} and recreating it empty ...")
    for sql in (
        f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)',
        f'CREATE DATABASE "{name}"',
    ):
        r = psql(maint, env, command=sql, capture=True)
        if r.returncode != 0:
            sys.exit(f"Reset failed on: {sql}\n{r.stderr.strip()}")
    print("Database recreated.\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the order and stop")
    parser.add_argument("--no-stamp", action="store_true", help="do not run `alembic stamp head` at the end")
    parser.add_argument("--reset", action="store_true", help="drop the database and recreate it empty before applying")
    parser.add_argument(
        "--yes-drop-everything", action="store_true", help="required with --reset: confirms ALL data in the database is deleted"
    )
    parser.add_argument("--assume-applied-through", metavar="FILE", help="record files up to and including FILE as already applied")
    args = parser.parse_args()

    files = sql_files()
    if args.dry_run:
        for i, f in enumerate(files, 1):
            print(f"{i:3d}  {f.name}")
        print(f"\n{len(files)} files")
        return

    settings = get_settings()
    if not settings.migration_database_url or not settings.database_url:
        sys.exit("MIGRATION_DATABASE_URL and DATABASE_URL must both be set in backend/.env")
    admin = make_url(settings.migration_database_url.replace("+asyncpg", ""))
    app = make_url(settings.database_url.replace("+asyncpg", ""))
    env = psql_env(admin)
    print(f"Target: {admin.host} / {admin.database} as {admin.username}")

    if args.reset:
        if not args.yes_drop_everything:
            sys.exit("--reset deletes the whole database. Add --yes-drop-everything to confirm.")
        reset_database(admin, env)

    check = psql(admin, env, command="SELECT 1", capture=True)
    if check.returncode != 0:
        sys.exit(f"Cannot connect:\n{check.stderr.strip()}")

    psql(
        admin,
        env,
        command="CREATE TABLE IF NOT EXISTS public._sql_v1_applied (file text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())",
    )

    if args.assume_applied_through:
        names = [f.name for f in files]
        if args.assume_applied_through not in names:
            sys.exit(f"{args.assume_applied_through} is not one of the SQL/v1 files")
        through = names[: names.index(args.assume_applied_through) + 1]
        values = ",".join(f"('{n}')" for n in through)
        psql(admin, env, command=f"INSERT INTO public._sql_v1_applied (file) VALUES {values} ON CONFLICT DO NOTHING")
        print(f"Marked {len(through)} files as already applied (through {args.assume_applied_through})")

    done = set(psql(admin, env, command="SELECT file FROM public._sql_v1_applied", capture=True).stdout.split())

    applied = 0
    for i, f in enumerate(files, 1):
        if f.name in done:
            continue
        if f.name == SEED_BEFORE and SEED_KEY not in done:
            print(f"[seed ] {SEED_MODULE} ... ", end="", flush=True)
            r = subprocess.run([sys.executable, "-m", SEED_MODULE], cwd=BACKEND_DIR, capture_output=True, text=True)
            if r.returncode != 0:
                print("FAILED")
                print((r.stderr or r.stdout).strip()[-1500:])
                sys.exit("\nStopped at the PRS content seed. Fix the cause and run the same command again.")
            psql(admin, env, command=f"INSERT INTO public._sql_v1_applied (file) VALUES ('{SEED_KEY}') ON CONFLICT DO NOTHING")
            done.add(SEED_KEY)
            print("ok")
        print(f"[{i:3d}/{len(files)}] {f.name} ... ", end="", flush=True)
        result = psql(admin, env, file=f, capture=True)
        if result.returncode != 0:
            print("FAILED")
            print(result.stderr.strip())
            print(f"\nStopped at {f.name}. Fix the cause and run the same command again to continue.")
            sys.exit(1)
        psql(admin, env, command=f"INSERT INTO public._sql_v1_applied (file) VALUES ('{f.name}') ON CONFLICT DO NOTHING")
        print("ok")
        applied += 1

        if f.name == CREATE_APP_ROLE_AFTER:
            pw = (app.password or "").replace("'", "''")
            role_sql = (
                "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anava_app') THEN "
                f"CREATE ROLE anava_app LOGIN PASSWORD '{pw}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS; "
                f"ELSE ALTER ROLE anava_app PASSWORD '{pw}'; END IF; END $$;"
            )
            for role in RANDOMIZE_ROLES:
                role_sql += f" ALTER ROLE {role} PASSWORD '{secrets.token_hex(24)}';"
            r = psql(admin, env, command=role_sql, capture=True)
            if r.returncode != 0:
                sys.exit(f"Could not set up roles:\n{r.stderr.strip()}")
            print("          roles: anava_app created, placeholder role passwords randomized")

    print(f"\nApplied {applied} file(s) this run; {len(done)} were already recorded.")

    tables = psql(
        admin,
        env,
        capture=True,
        command=(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema IN ('core','reference','compliance','ops','analytics') AND table_type='BASE TABLE'"
        ),
    ).stdout.strip()
    print(f"Tables in the application schemas: {tables}")

    if not args.no_stamp:
        print("Recording the schema version: alembic stamp head")
        r = subprocess.run(["alembic", "stamp", "head"], cwd=BACKEND_DIR, capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip()[-400:])
        if r.returncode != 0:
            sys.exit("alembic stamp failed — the schema is built, but alembic does not know it yet")


if __name__ == "__main__":
    main()
