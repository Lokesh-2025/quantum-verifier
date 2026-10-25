"""
cloud_snapshot.py
------------------
GitHub Actions cloud collector for quantum-verifier — runs every 2 hours,
independent of whether the laptop is on. Added 2026-08-30 to close a real
reliability gap: quantum-hardware-mcp has had a cloud-backed collector
since 2026-06-11 (laptop LaunchAgent every 6h + GitHub Actions every 2h);
quantum-verifier had ONLY the laptop path, so any stretch with the laptop
off/closed was a real, permanent gap in its own history.

Writes straight to Turso (the single store); nothing is committed back to
the repo. It used to append CSVs, until data/pair_snapshots.csv hit
GitHub's 100 MB file limit on 2026-10-17 and every push was rejected.

Reuses providers/ibm.py's existing, tested list_devices()/get_device_details()
unchanged. They still write to a local SQLite db as a side effect (via
_save_snapshots / _save_qubit_and_pair_snapshot) — in this CI context that
db is fresh/empty each run, so this script reads back what was JUST
written and sends it to Turso:

    device_snapshots      -- device-level (IBM + IonQ)
    qubit_snapshots       -- per-qubit history (IBM only)
    pair_snapshots        -- per 2-qubit-gate history (IBM only)
    gate1q_snapshots      -- per 1-qubit-gate history: sx/x/id/rz (IBM only)
    raw_properties_archive-- full raw IBM calibration record, once per recalibration
    ionq_extras           -- IonQ status, queue time, 1q fidelity, timings
    collector_runs        -- one health row per run

IonQ is device-level only, matching what IonQ's public API actually
exposes (no per-qubit detail the way IBM's does). Uses the FIXED
characterization-fetch pattern: quantum-hardware-mcp's own IonQ collector
had a real bug until 2026-08-22 — it read b.get("characterization")
directly, which IonQ's /v0.3/backends response never populates; the real
fidelity numbers live behind a separate characterization_url needing its
own follow-up request. Ported here already fixed, not the broken version.

Run manually:
    .venv/bin/python cloud_snapshot.py

Or let .github/workflows/snapshot.yml call it every 2 hours.
"""
import base64
import gzip
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

from providers.ibm import list_devices, get_device_details, DB_PATH as IBM_DB_PATH

DEVICE_FIELDS = ["ts", "provider", "name", "num_qubits", "operational",
                  "pending_jobs", "avg_cx_error", "avg_readout_error",
                  "avg_t1_us", "avg_t2_us"]
QUBIT_FIELDS = ["device_name", "qubit_index", "property_name", "value", "unit",
                 "vendor_measured_at", "polled_at"]
PAIR_FIELDS = ["device_name", "qubit1", "qubit2", "gate_name", "property_name",
                "value", "unit", "vendor_measured_at", "polled_at"]
RAW_FIELDS = ["device_name", "vendor_measured_at", "raw_json_gzip", "polled_at"]


def collect_ibm() -> tuple:  # (device, qubit, pair, gate1q, raw) rows
    """Collect IBM device + qubit + pair data via the existing, tested
    provider functions, then export whatever they just wrote locally into
    the CSVs. The local db is fresh each CI run and is never committed."""
    device_rows, qubit_rows, pair_rows, gate1q_rows, raw_rows = [], [], [], [], []
    ts = datetime.now(timezone.utc).isoformat()
    try:
        devices = list_devices()
    except Exception as e:
        print(f"[IBM] list_devices failed: {e}", file=sys.stderr)
        return device_rows, qubit_rows, pair_rows, gate1q_rows, raw_rows

    for d in devices:
        name = d.get("name")
        if not name:
            continue
        try:
            details = get_device_details(name)
        except Exception as e:
            print(f"[IBM] get_device_details({name}) failed: {e}", file=sys.stderr)
            continue
        device_rows.append({
            "ts": ts, "provider": "ibm", "name": details.get("name"),
            "num_qubits": details.get("num_qubits"),
            "operational": int(details["operational"]) if details.get("operational") is not None else None,
            "pending_jobs": details.get("pending_jobs"),
            "avg_cx_error": details.get("avg_cx_error"),
            "avg_readout_error": details.get("avg_readout_error"),
            "avg_t1_us": details.get("avg_t1_us"),
            "avg_t2_us": details.get("avg_t2_us"),
        })

    # Export whatever this run just wrote into the (fresh, per-run) local db
    # -- not the whole table's history, just this run's rows.
    try:
        with sqlite3.connect(IBM_DB_PATH) as con:
            for row in con.execute(
                "SELECT device_name, qubit_index, property_name, value, unit, "
                "vendor_measured_at, polled_at FROM qubit_snapshots WHERE polled_at >= ?", (ts,)
            ):
                qubit_rows.append(dict(zip(QUBIT_FIELDS, row)))
            for row in con.execute(
                "SELECT device_name, qubit1, qubit2, gate_name, property_name, value, unit, "
                "vendor_measured_at, polled_at FROM pair_snapshots WHERE polled_at >= ?", (ts,)
            ):
                pair_rows.append(dict(zip(PAIR_FIELDS, row)))
            for row in con.execute(
                "SELECT device_name, vendor_measured_at, raw_json_gzip, polled_at "
                "FROM raw_properties_archive WHERE polled_at >= ?", (ts,)
            ):
                raw = dict(zip(RAW_FIELDS, row))
                raw_rows.append(raw)
                gate1q_rows.extend(_single_qubit_gates(raw))
    except Exception as e:
        print(f"[IBM] reading back qubit/pair/raw rows failed: {e}", file=sys.stderr)

    return device_rows, qubit_rows, pair_rows, gate1q_rows, raw_rows


def _single_qubit_gates(raw: dict) -> list:
    """providers/ibm.py's per-pair export skips every gate that isn't 2-qubit,
    so sx/x/id/rz error and length were never kept. The full gate list is
    already inside the raw properties record it archives, so unpack the
    1-qubit ones from there -- no extra IBM API call."""
    rows = []
    data = json.loads(gzip.decompress(raw["raw_json_gzip"]))
    for g in data["gates"]:
        if len(g["qubits"]) != 1:
            continue
        for p in g["parameters"]:
            rows.append({
                "device_name": raw["device_name"], "qubit_index": g["qubits"][0],
                "gate_name": g["gate"], "property_name": p["name"], "value": p["value"],
                "unit": p["unit"], "vendor_measured_at": p["date"], "polled_at": raw["polled_at"],
            })
    return rows


def collect_ionq() -> tuple:  # (device rows, extras rows)
    """
    Device-level only -- IonQ's public API doesn't expose per-qubit detail
    the way IBM's does. Uses the FIXED characterization-fetch pattern (see
    module docstring): quantum-hardware-mcp's own collector silently logged
    354 null-calibration rows before this exact fix landed there 2026-08-22.
    """
    api_key = os.getenv("IONQ_API_KEY")
    if not api_key:
        print("[IonQ] IONQ_API_KEY not set — skipping", file=sys.stderr)
        return [], []

    headers = {"Authorization": f"apiKey {api_key}"}
    ts = datetime.now(timezone.utc).isoformat()
    try:
        resp = requests.get("https://api.ionq.co/v0.3/backends", headers=headers, timeout=15)
        resp.raise_for_status()
        backends = resp.json()
    except Exception as e:
        print(f"[IonQ] Failed to fetch backends: {e}", file=sys.stderr)
        return [], []

    rows, extras = [], []
    for b in backends:
        char_url = b.get("characterization_url")
        fidelity, timing, char_date = {}, {}, None
        if char_url:
            try:
                char_resp = requests.get(f"https://api.ionq.co/v0.3{char_url}", headers=headers, timeout=15)
                if char_resp.status_code == 200:
                    char = char_resp.json()
                    fidelity = char.get("fidelity", {}) or {}
                    timing = char.get("timing", {}) or {}
                    char_date = char.get("date")
            except Exception as e:
                print(f"[IonQ] characterization fetch failed for {b.get('backend')}: {e}", file=sys.stderr)

        rows.append({
            "ts": ts, "provider": "ionq",
            "name": b.get("backend", b.get("name", "unknown")),
            "num_qubits": b.get("qubits"),
            "operational": 1 if b.get("status") == "available" else 0,
            "pending_jobs": None,
            "avg_cx_error": round(1 - fidelity["2q"]["mean"], 5)
                if fidelity.get("2q", {}).get("mean") else None,
            "avg_readout_error": round(1 - fidelity.get("spam", {}).get("mean", 1), 5)
                if fidelity.get("spam", {}).get("mean") else None,
            "avg_t1_us": round(timing["t1"] * 1e6, 3) if timing.get("t1") else None,
            "avg_t2_us": round(timing["t2"] * 1e6, 3) if timing.get("t2") else None,
        })
        # The device row above has no room for these, and device_snapshots is
        # read by providers/ibm.py with SELECT *, so they live in their own table.
        extras.append({
            "ts": ts, "name": b.get("backend", b.get("name", "unknown")),
            "status": b.get("status"), "degraded": int(bool(b.get("degraded"))),
            "avg_queue_time": b.get("average_queue_time"),
            "fidelity_1q_mean": (fidelity.get("1q") or {}).get("mean"),
            "fidelity_1q_median": (fidelity.get("1q") or {}).get("median"),
            "fidelity_2q_median": (fidelity.get("2q") or {}).get("median"),
            "fidelity_spam_median": (fidelity.get("spam") or {}).get("median"),
            "time_readout_s": timing.get("readout"), "time_reset_s": timing.get("reset"),
            "time_1q_s": timing.get("1q"), "time_2q_s": timing.get("2q"),
            "characterization_date": char_date, "last_updated": b.get("last_updated"),
        })
    print(f"[IonQ] Collected {len(rows)} backends")
    return rows, extras


def collect() -> None:
    started = datetime.now(timezone.utc).isoformat()
    print(f"[{started}] Starting quantum-verifier cloud snapshot...")
    ibm_device_rows, qubit_rows, pair_rows, gate1q_rows, raw_rows = collect_ibm()
    ionq_device_rows, ionq_extras = collect_ionq()

    print(f"Collected {len(ibm_device_rows)} IBM + {len(ionq_device_rows)} IonQ device rows, "
          f"{len(qubit_rows)} qubit, {len(pair_rows)} pair, {len(gate1q_rows)} 1q-gate rows, "
          f"{len(raw_rows)} raw records.")

    _write_turso(started, ibm_device_rows + ionq_device_rows, qubit_rows, pair_rows,
                 gate1q_rows, raw_rows, ionq_extras)


TURSO_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS gate1q_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_name TEXT NOT NULL, qubit_index INTEGER NOT NULL, gate_name TEXT NOT NULL,
        property_name TEXT NOT NULL, value REAL, unit TEXT,
        vendor_measured_at TEXT NOT NULL, polled_at TEXT NOT NULL,
        UNIQUE(device_name, qubit_index, gate_name, property_name, vendor_measured_at))""",
    """CREATE TABLE IF NOT EXISTS raw_properties_archive (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_name TEXT NOT NULL, vendor_measured_at TEXT NOT NULL,
        raw_json_gzip_b64 TEXT NOT NULL, polled_at TEXT NOT NULL,
        UNIQUE(device_name, vendor_measured_at))""",
    """CREATE TABLE IF NOT EXISTS ionq_extras (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, name TEXT NOT NULL, status TEXT, degraded INTEGER,
        avg_queue_time REAL, fidelity_1q_mean REAL, fidelity_1q_median REAL,
        fidelity_2q_median REAL, fidelity_spam_median REAL,
        time_readout_s REAL, time_reset_s REAL, time_1q_s REAL, time_2q_s REAL,
        characterization_date INTEGER, last_updated INTEGER)""",
    """CREATE TABLE IF NOT EXISTS collector_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL, finished_at TEXT NOT NULL, ok INTEGER NOT NULL,
        ibm_devices INTEGER, ionq_backends INTEGER, qubit_rows INTEGER, pair_rows INTEGER,
        gate1q_rows INTEGER, raw_new INTEGER, error TEXT)""",
]


def _write_turso(started: str, device_rows: list, qubit_rows: list, pair_rows: list,
                 gate1q_rows: list, raw_rows: list, ionq_extras: list) -> None:
    """
    Turso is the single store: this is what get_alerts() and anything else
    calling _run_query() (providers/ibm.py) reads at query time, from
    anywhere, with no sync step. Any failure here exits non-zero so the
    GitHub Actions run goes red instead of silently losing a snapshot.

    Uses core/turso.py's plain requests-based client, not libsql_client --
    see that module's docstring for why (a confirmed hang-on-exit bug in
    libsql_client's sync wrapper).
    """
    from core.turso import is_configured, execute, execute_batch

    if not is_configured():
        print("TURSO_DATABASE_URL/TURSO_AUTH_TOKEN not set — nothing was saved.", file=sys.stderr)
        sys.exit(1)

    BATCH = 500
    n_ibm = sum(1 for r in device_rows if r["provider"] == "ibm")
    n_ionq = sum(1 for r in device_rows if r["provider"] == "ionq")
    raw_new, error = 0, None

    def _chunked_batch(statements):
        for i in range(0, len(statements), BATCH):
            execute_batch(statements[i:i + BATCH])

    try:
        for stmt in TURSO_SCHEMA:
            execute(stmt)

        _chunked_batch([
            ("INSERT INTO device_snapshots (ts, provider, name, num_qubits, operational, "
             "pending_jobs, avg_cx_error, avg_readout_error, median_t1_us, median_t2_us) "
             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
             (r["ts"], r["provider"], r["name"], r["num_qubits"], r["operational"],
              r["pending_jobs"], r["avg_cx_error"], r["avg_readout_error"],
              r["avg_t1_us"], r["avg_t2_us"]))
            for r in device_rows
        ])
        _chunked_batch([
            ("INSERT OR IGNORE INTO qubit_snapshots (device_name, qubit_index, property_name, "
             "value, unit, vendor_measured_at, polled_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
             (r["device_name"], r["qubit_index"], r["property_name"], r["value"],
              r["unit"], r["vendor_measured_at"], r["polled_at"]))
            for r in qubit_rows
        ])
        _chunked_batch([
            ("INSERT OR IGNORE INTO pair_snapshots (device_name, qubit1, qubit2, gate_name, "
             "property_name, value, unit, vendor_measured_at, polled_at) "
             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
             (r["device_name"], r["qubit1"], r["qubit2"], r["gate_name"], r["property_name"],
              r["value"], r["unit"], r["vendor_measured_at"], r["polled_at"]))
            for r in pair_rows
        ])
        _chunked_batch([
            ("INSERT OR IGNORE INTO gate1q_snapshots (device_name, qubit_index, gate_name, "
             "property_name, value, unit, vendor_measured_at, polled_at) "
             "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
             (r["device_name"], r["qubit_index"], r["gate_name"], r["property_name"],
              r["value"], r["unit"], r["vendor_measured_at"], r["polled_at"]))
            for r in gate1q_rows
        ])
        _chunked_batch([
            ("INSERT INTO ionq_extras (ts, name, status, degraded, avg_queue_time, "
             "fidelity_1q_mean, fidelity_1q_median, fidelity_2q_median, fidelity_spam_median, "
             "time_readout_s, time_reset_s, time_1q_s, time_2q_s, characterization_date, "
             "last_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
             (r["ts"], r["name"], r["status"], r["degraded"], r["avg_queue_time"],
              r["fidelity_1q_mean"], r["fidelity_1q_median"], r["fidelity_2q_median"],
              r["fidelity_spam_median"], r["time_readout_s"], r["time_reset_s"],
              r["time_1q_s"], r["time_2q_s"], r["characterization_date"], r["last_updated"]))
            for r in ionq_extras
        ])

        # The raw record only changes when IBM recalibrates (~daily), so only
        # upload the ones Turso doesn't already hold.
        have = {(a, b) for a, b in execute(
            "SELECT device_name, vendor_measured_at FROM raw_properties_archive")}
        new_raw = [r for r in raw_rows if (r["device_name"], r["vendor_measured_at"]) not in have]
        for r in new_raw:  # one at a time: each is large
            execute_batch([(
                "INSERT OR IGNORE INTO raw_properties_archive (device_name, vendor_measured_at, "
                "raw_json_gzip_b64, polled_at) VALUES (?, ?, ?, ?)",
                (r["device_name"], r["vendor_measured_at"],
                 base64.b64encode(r["raw_json_gzip"]).decode("ascii"), r["polled_at"]))])
        raw_new = len(new_raw)

        if n_ibm == 0:
            raise RuntimeError("no IBM devices were collected (IBM API or token problem)")
        print(f"Turso: wrote {len(device_rows)} device, {len(qubit_rows)} qubit, "
              f"{len(pair_rows)} pair, {len(gate1q_rows)} 1q-gate rows, {raw_new} new raw records, "
              f"{len(ionq_extras)} IonQ extras — live for anyone running the tool now.")
    except Exception as e:
        error = str(e)
        print(f"Turso write failed: {e}", file=sys.stderr)

    try:  # health log, written even when the run above failed
        execute_batch([(
            "INSERT INTO collector_runs (started_at, finished_at, ok, ibm_devices, ionq_backends, "
            "qubit_rows, pair_rows, gate1q_rows, raw_new, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (started, datetime.now(timezone.utc).isoformat(), 0 if error else 1, n_ibm, n_ionq,
             len(qubit_rows), len(pair_rows), len(gate1q_rows), raw_new, error))])
    except Exception as e:
        print(f"collector_runs log failed: {e}", file=sys.stderr)
    if error:
        sys.exit(1)


if __name__ == "__main__":
    collect()
