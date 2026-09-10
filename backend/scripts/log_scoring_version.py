"""Snapshots PRS scale scoring logic from app/modules/prs/scoring_rules.py
into reference.scoring_logic_versions — an append-only audit log. Scoring
itself always executes straight from that file; this table is never read at
scoring time, it's purely a timestamped history of what the logic *was*,
per scale_code.

Run this by hand any time scoring_rules.py changes for a scale, so the log
never drifts from the real code. Also doubles as the one-time seed for
scales that don't have any version logged yet (--all, first run).

Usage:
    python -m scripts.log_scoring_version --all --reason "initial snapshot"
    python -m scripts.log_scoring_version --scale-code GAD-7 --reason "..."
    python -m scripts.log_scoring_version --list GAD-7
    python -m scripts.log_scoring_version --show GAD-7 --version 1

Rollback: --show the old version, manually restore scoring_rules.py to
match its config/source_code, then re-run --scale-code to log that restored
state as a new version. History is never edited or deleted, only appended to.
"""

import argparse
import asyncio
import hashlib
import inspect
import json
import subprocess
from pathlib import Path

from sqlalchemy import text

from app.core.db import get_migration_engine
from app.modules.prs.scoring_rules import _RISK_THRESHOLDS, SCALE_CONFIG, SPECIAL_SCORERS

REPO_ROOT = Path(__file__).resolve().parents[2]


def current_git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def snapshot_for(scale_code: str) -> tuple[str, dict | None, str | None]:
    if scale_code in SPECIAL_SCORERS:
        return "special_scorer", None, inspect.getsource(SPECIAL_SCORERS[scale_code])
    if scale_code not in SCALE_CONFIG:
        raise ValueError(f"{scale_code!r} is not in SCALE_CONFIG or SPECIAL_SCORERS — check the scale_code")
    config = dict(SCALE_CONFIG[scale_code])
    if scale_code in _RISK_THRESHOLDS:
        config["risk_threshold"] = list(_RISK_THRESHOLDS[scale_code])
    return "config", config, None


def content_hash(config: dict | None, source_code: str | None) -> str:
    payload = json.dumps(config, sort_keys=True) if config is not None else (source_code or "")
    return hashlib.sha256(payload.encode()).hexdigest()


async def log_scale(conn, scale_code: str, *, reason: str, changed_by: str | None) -> str:
    logic_kind, config, source_code = snapshot_for(scale_code)
    new_hash = content_hash(config, source_code)

    active = (
        (
            await conn.execute(
                text('SELECT "config", "source_code" FROM reference."scoring_logic_versions" WHERE "scale_code" = :s AND "is_active"'),
                {"s": scale_code},
            )
        )
        .mappings()
        .first()
    )
    if active is not None:
        if content_hash(active["config"], active["source_code"]) == new_hash:
            return f"{scale_code}: unchanged, skipped"

    next_version = (
        await conn.execute(
            text('SELECT COALESCE(MAX("version"), 0) + 1 FROM reference."scoring_logic_versions" WHERE "scale_code" = :s'),
            {"s": scale_code},
        )
    ).scalar_one()

    await conn.execute(
        text('UPDATE reference."scoring_logic_versions" SET "is_active" = false WHERE "scale_code" = :s AND "is_active"'),
        {"s": scale_code},
    )
    await conn.execute(
        text(
            'INSERT INTO reference."scoring_logic_versions" '
            '("scale_code", "version", "logic_kind", "config", "source_code", "git_commit", "is_active", "changed_by", "change_reason") '
            "VALUES (:s, :v, :kind, CAST(:config AS JSONB), :source, :commit, true, :changed_by, :reason)"
        ),
        {
            "s": scale_code,
            "v": next_version,
            "kind": logic_kind,
            "config": json.dumps(config) if config is not None else None,
            "source": source_code,
            "commit": current_git_commit(),
            "changed_by": changed_by,
            "reason": reason,
        },
    )
    return f"{scale_code}: logged v{next_version} ({logic_kind})"


async def show_version(conn, scale_code: str, version: int | None) -> None:
    query = 'SELECT * FROM reference."scoring_logic_versions" WHERE "scale_code" = :s'
    params = {"s": scale_code}
    if version is not None:
        query += ' AND "version" = :v'
        params["v"] = version
    else:
        query += ' AND "is_active"'
    row = (await conn.execute(text(query), params)).mappings().first()
    if row is None:
        print(f"No matching version for {scale_code}" + (f" v{version}" if version else " (active)"))
        return
    print(f"{scale_code} v{row['version']} ({row['logic_kind']}) — {row['change_reason'] or 'no reason logged'}")
    print(f"created_at={row['created_at']} git_commit={row['git_commit']} changed_by={row['changed_by']}")
    if row["config"] is not None:
        print(json.dumps(row["config"], indent=2))
    else:
        print(row["source_code"])


async def list_versions(conn, scale_code: str) -> None:
    rows = (
        (
            await conn.execute(
                text(
                    'SELECT "version", "logic_kind", "is_active", "created_at", "change_reason" '
                    'FROM reference."scoring_logic_versions" WHERE "scale_code" = :s ORDER BY "version"'
                ),
                {"s": scale_code},
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        print(f"No versions logged for {scale_code} yet")
        return
    for r in rows:
        marker = "*" if r["is_active"] else " "
        print(f"{marker} v{r['version']} ({r['logic_kind']}) {r['created_at']} — {r['change_reason'] or ''}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scale-code", help="Log the current code state for this one scale")
    parser.add_argument("--all", action="store_true", help="Log the current code state for every scale in SCALE_CONFIG")
    parser.add_argument("--reason", help="Why this version changed (required when logging)")
    parser.add_argument("--changed-by", help="profiles.id of whoever made the change, if known")
    parser.add_argument("--list", metavar="SCALE_CODE", help="List all logged versions for a scale")
    parser.add_argument("--show", metavar="SCALE_CODE", help="Print one version's full config/source")
    parser.add_argument("--version", type=int, help="Version number for --show (defaults to the active one)")
    args = parser.parse_args()

    engine = get_migration_engine()
    async with engine.begin() as conn:
        if args.list:
            await list_versions(conn, args.list)
        elif args.show:
            await show_version(conn, args.show, args.version)
        elif args.all or args.scale_code:
            if not args.reason:
                parser.error("--reason is required when logging a version")
            scale_codes = list(SCALE_CONFIG.keys()) if args.all else [args.scale_code]
            for scale_code in scale_codes:
                print(await log_scale(conn, scale_code, reason=args.reason, changed_by=args.changed_by))
        else:
            parser.error("pass --all, --scale-code, --list, or --show")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
