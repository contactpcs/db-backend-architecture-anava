"""Performance probe for load-test investigations. OFF unless PERF_PROBE=1.

Measures, per request, where the time goes: waiting for a pooled database
connection, inside database calls, and everything else. Also samples the
connection pool, event-loop delay and process/machine load twice a second.

Writes two JSON-lines files under settings.perf_probe_dir:
    requests.jsonl   one line per request
    gauges.jsonl     one line per 0.5 s sample

Logs route templates, never raw paths, query strings, headers or bodies, so
no token, id or patient data is written. Changes no request behaviour.
main.py only wires it in when settings.perf_probe is true.
"""

import asyncio
import json
import os
import time
from contextvars import ContextVar
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# A mutable dict: the database listeners below run inside SQLAlchemy's
# greenlet (and BaseHTTPMiddleware runs the app in a child task), both of
# which see the same object through the copied context.
_current: ContextVar[dict | None] = ContextVar("perf_probe_request", default=None)
_request_lines: list[str] = []
_state = {"in_flight": 0, "pool_waiting": 0}


def _new_record() -> dict:
    return {"pool_wait_ms": 0.0, "pool_checkouts": 0, "new_connections": 0, "db_ms": 0.0, "db_statements": 0, "conn_held_ms": 0.0}


class PerfProbeMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        record = _new_record()
        token = _current.set(record)
        started_wall, started = time.time(), time.perf_counter()
        status = 0
        _state["in_flight"] += 1

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _state["in_flight"] -= 1
            _current.reset(token)
            total = (time.perf_counter() - started) * 1000
            route = scope.get("route")
            _request_lines.append(
                json.dumps(
                    {
                        "ts": round(started_wall, 3),
                        "method": scope.get("method"),
                        "route": getattr(route, "path", None) or "(no route: rejected before routing)",
                        "status": status,
                        "total_ms": round(total, 1),
                        "pool_wait_ms": round(record["pool_wait_ms"], 1),
                        "db_ms": round(record["db_ms"], 1),
                        "other_ms": round(total - record["pool_wait_ms"] - record["db_ms"], 1),
                        "conn_held_ms": round(record["conn_held_ms"], 1),
                        "db_statements": record["db_statements"],
                        "pool_checkouts": record["pool_checkouts"],
                        "new_connections": record["new_connections"],
                    }
                )
            )


def install(engine: AsyncEngine) -> None:
    """Hooks the request pool. Call once at import time when the probe is on."""
    sync_engine = engine.sync_engine
    pool = sync_engine.pool
    original_connect = pool.connect

    def timed_connect():
        # Covers the whole acquisition: queueing for a free connection, the
        # liveness ping when one is due, and opening a new connection.
        started = time.perf_counter()
        _state["pool_waiting"] += 1
        try:
            return original_connect()
        finally:
            _state["pool_waiting"] -= 1
            record = _current.get()
            if record is not None:
                record["pool_wait_ms"] += (time.perf_counter() - started) * 1000
                record["pool_checkouts"] += 1

    pool.connect = timed_connect  # type: ignore[method-assign]

    @event.listens_for(sync_engine, "connect")
    def _on_connect(dbapi_connection, connection_record) -> None:
        record = _current.get()
        if record is not None:
            record["new_connections"] += 1

    @event.listens_for(sync_engine, "checkout")
    def _on_checkout(dbapi_connection, connection_record, connection_proxy) -> None:
        connection_record.info["probe_out_at"] = time.perf_counter()
        connection_record.info["probe_record"] = _current.get()

    @event.listens_for(sync_engine, "checkin")
    def _on_checkin(dbapi_connection, connection_record) -> None:
        out_at = connection_record.info.pop("probe_out_at", None)
        record = connection_record.info.pop("probe_record", None)
        if out_at is not None and record is not None:
            record["conn_held_ms"] += (time.perf_counter() - out_at) * 1000

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany) -> None:
        context._probe_started = time.perf_counter()

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany) -> None:
        record = _current.get()
        started = getattr(context, "_probe_started", None)
        if record is not None and started is not None:
            record["db_ms"] += (time.perf_counter() - started) * 1000
            record["db_statements"] += 1


async def run_sampler(engine: AsyncEngine, directory: str) -> None:
    """Every 0.5 s: pool state, event-loop delay, process and machine load.
    Also the only place the request lines are written to disk, so the
    request path itself never touches a file."""
    import psutil

    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    requests_file, gauges_file = out / "requests.jsonl", out / "gauges.jsonl"
    process = psutil.Process(os.getpid())
    process.cpu_percent(None)
    psutil.cpu_percent(None)
    net_before = psutil.net_io_counters()
    interval = 0.5
    pool = engine.sync_engine.pool
    while True:
        slept_at = time.perf_counter()
        await asyncio.sleep(interval)
        lag_ms = (time.perf_counter() - slept_at - interval) * 1000
        net = psutil.net_io_counters()
        gauge = {
            "ts": round(time.time(), 3),
            "loop_lag_ms": round(lag_ms, 1),
            "in_flight": _state["in_flight"],
            "pool_checked_out": pool.checkedout(),  # type: ignore[attr-defined]
            "pool_size": pool.size(),  # type: ignore[attr-defined]
            "pool_overflow": pool.overflow(),  # type: ignore[attr-defined]
            "pool_waiting": _state["pool_waiting"],
            "proc_cpu_pct_of_one_core": process.cpu_percent(None),
            "proc_rss_mb": round(process.memory_info().rss / 1e6, 1),
            "machine_cpu_pct": psutil.cpu_percent(None),
            "machine_mem_pct": psutil.virtual_memory().percent,
            "net_recv_kbps": round((net.bytes_recv - net_before.bytes_recv) * 8 / 1000 / interval),
            "net_sent_kbps": round((net.bytes_sent - net_before.bytes_sent) * 8 / 1000 / interval),
        }
        net_before = net
        lines, _request_lines[:] = list(_request_lines), []
        with gauges_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(gauge) + "\n")
        if lines:
            with requests_file.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
