"""
core/turso.py
--------------
Thin client for the shared Turso database, added 2026-08-30 so device
history is queryable live from anywhere instead of only from whoever's
laptop last ran a local import.

Uses Turso's raw HTTP pipeline API via `requests` directly, NOT the
`libsql_client` package. Real bug found and confirmed while building this:
libsql_client's sync wrapper spins up a background thread running an
asyncio event loop, and that thread does not reliably terminate on
process exit -- even calling .close() via atexit still hangs indefinitely
(confirmed directly: an inline, explicit .close() call exits in ~5s;
the identical .close() registered via atexit.register() still hangs past
90s). That's a real risk for the long-running MCP server process, not
just test scripts. Plain `requests.post()` has no background threads at
all, so this whole class of bug doesn't exist here.

Every call is stateless (one HTTP request per call, closes the Turso-side
connection itself via a trailing "close" pipeline step) -- simpler than
connection pooling, and this data doesn't need low-latency chains of
queries in one transaction.
"""
import os

import requests


def _to_arg(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}


def _from_cell(cell):
    t = cell.get("type")
    v = cell.get("value")
    if t == "null":
        return None
    if t == "integer":
        return int(v)
    if t == "float":
        return float(v)
    return v


def is_configured() -> bool:
    return bool(os.getenv("TURSO_DATABASE_URL") and os.getenv("TURSO_AUTH_TOKEN"))


def execute(sql: str, params: tuple = ()) -> list:
    """Run one SQL statement, return rows as a list of tuples. Raises on
    any failure (missing config, network error, Turso-side SQL error) --
    callers decide how to fall back, this doesn't swallow errors itself."""
    url = os.getenv("TURSO_DATABASE_URL")
    token = os.getenv("TURSO_AUTH_TOKEN")
    if not url or not token:
        raise RuntimeError("TURSO_DATABASE_URL/TURSO_AUTH_TOKEN not set")

    resp = requests.post(
        f"{url.replace('libsql://', 'https://')}/v2/pipeline",
        headers={"Authorization": f"Bearer {token}"},
        json={"requests": [
            {"type": "execute", "stmt": {"sql": sql, "args": [_to_arg(p) for p in params]}},
            {"type": "close"},
        ]},
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()["results"][0]
    if result["type"] == "error":
        raise RuntimeError(result["error"]["message"])
    result_rows = result["response"]["result"]["rows"]
    return [tuple(_from_cell(cell) for cell in row) for row in result_rows]


def execute_batch(statements: list) -> None:
    """Run many (sql, params) writes in one HTTP request.

    Real perf bug found and fixed 2026-08-31: sending N statements as N
    separate pipeline steps measured ~9.7s for a 500-row batch (~50
    rows/sec) -- confirmed directly. Combining same-SQL rows into ONE
    multi-row `INSERT ... VALUES (?,?,...), (?,?,...), ...` statement
    measured ~0.35s for the same 500 rows (~1,400 rows/sec) -- a ~28x
    difference. Every real caller in this codebase already passes one
    repeated SQL template per batch call, so that's the fast path here;
    a batch mixing different SQL templates falls back to one pipeline
    step per statement (still correct, just not accelerated).

    Raises on any failure so callers know a batch is all-or-nothing
    reported, not silently partial."""
    url = os.getenv("TURSO_DATABASE_URL")
    token = os.getenv("TURSO_AUTH_TOKEN")
    if not url or not token:
        raise RuntimeError("TURSO_DATABASE_URL/TURSO_AUTH_TOKEN not set")
    if not statements:
        return

    sqls = {sql for sql, _ in statements}
    if len(sqls) == 1:
        # Fast path: one multi-row INSERT instead of N single-row ones.
        sql = next(iter(sqls))
        n_params = len(statements[0][1])
        row_placeholder = "(" + ", ".join("?" for _ in range(n_params)) + ")"
        multi_values = ", ".join(row_placeholder for _ in statements)
        # Every real caller's SQL ends in "VALUES (?, ?, ..., ?)" -- replace
        # that single-row placeholder group with N of them.
        single_placeholder = row_placeholder
        if single_placeholder not in sql:
            raise RuntimeError(
                f"execute_batch fast path expected a literal '{single_placeholder}' "
                f"in the SQL to expand into a multi-row VALUES clause: {sql!r}"
            )
        combined_sql = sql.replace(single_placeholder, multi_values)
        combined_args = [_to_arg(v) for _, params in statements for v in params]
        requests_body = [
            {"type": "execute", "stmt": {"sql": combined_sql, "args": combined_args}},
            {"type": "close"},
        ]
    else:
        requests_body = [
            {"type": "execute", "stmt": {"sql": sql, "args": [_to_arg(p) for p in params]}}
            for sql, params in statements
        ] + [{"type": "close"}]

    resp = requests.post(
        f"{url.replace('libsql://', 'https://')}/v2/pipeline",
        headers={"Authorization": f"Bearer {token}"},
        json={"requests": requests_body},
        timeout=60,
    )
    resp.raise_for_status()
    for r in resp.json()["results"]:
        if r["type"] == "error":
            raise RuntimeError(r["error"]["message"])
