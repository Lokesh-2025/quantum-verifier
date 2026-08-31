"""
cloud_snapshot.py
------------------
GitHub Actions cloud collector for quantum-verifier — runs every 2 hours,
independent of whether the laptop is on. Added 2026-08-30 to close a real
reliability gap: quantum-hardware-mcp has had a cloud-backed collector
since 2026-06-11 (laptop LaunchAgent every 6h + GitHub Actions every 2h);
quantum-verifier had ONLY the laptop path, so any stretch with the laptop
off/closed was a real, permanent gap in its own history.

Mirrors quantum-hardware-mcp's cloud/local split on purpose: writes to CSV
(git-committable, safe to append concurrently) rather than the local
SQLite database (ibm_history.db), which stays laptop-only — SQLite files
don't merge cleanly if two runs try to commit them at once, CSVs do.

Reuses providers/ibm.py's existing, tested list_devices()/get_device_details()
unchanged. They still write to a local SQLite db as a side effect (via
_save_snapshots / _save_qubit_and_pair_snapshot) — in this CI context that
db is fresh/empty each run and never committed (*.db is gitignored), so
this script reads back what was JUST written and appends it to three CSVs:

    data/device_snapshots.csv  -- device-level (IBM + IonQ)
    data/qubit_snapshots.csv   -- per-qubit history (IBM only)
    data/pair_snapshots.csv    -- per-connection-pair history (IBM only)

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
import csv
import os
import sqlite3
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

from providers.ibm import list_devices, get_device_details, DB_PATH as IBM_DB_PATH

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, "data")

DEVICE_CSV = os.path.join(DATA_DIR, "device_snapshots.csv")
QUBIT_CSV = os.path.join(DATA_DIR, "qubit_snapshots.csv")
PAIR_CSV = os.path.join(DATA_DIR, "pair_snapshots.csv")

DEVICE_FIELDS = ["ts", "provider", "name", "num_qubits", "operational",
                  "pending_jobs", "avg_cx_error", "avg_readout_error",
                  "avg_t1_us", "avg_t2_us"]
QUBIT_FIELDS = ["device_name", "qubit_index", "property_name", "value", "unit",
                 "vendor_measured_at", "polled_at"]
PAIR_FIELDS = ["device_name", "qubit1", "qubit2", "gate_name", "property_name",
                "value", "unit", "vendor_measured_at", "polled_at"]


def _append_csv(path: str, fields: list, rows: list) -> None:
    if not rows:
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    new_file = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new_file:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


def collect_ibm() -> tuple:
    """Collect IBM device + qubit + pair data via the existing, tested
    provider functions, then export whatever they just wrote locally into
    the CSVs. The local db is fresh each CI run and is never committed."""
    device_rows, qubit_rows, pair_rows = [], [], []
    ts = datetime.now(timezone.utc).isoformat()
    try:
        devices = list_devices()
    except Exception as e:
        print(f"[IBM] list_devices failed: {e}", file=sys.stderr)
        return device_rows, qubit_rows, pair_rows

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
    except Exception as e:
        print(f"[IBM] reading back qubit/pair rows for CSV export failed: {e}", file=sys.stderr)

    return device_rows, qubit_rows, pair_rows


def collect_ionq() -> list:
    """
    Device-level only -- IonQ's public API doesn't expose per-qubit detail
    the way IBM's does. Uses the FIXED characterization-fetch pattern (see
    module docstring): quantum-hardware-mcp's own collector silently logged
    354 null-calibration rows before this exact fix landed there 2026-08-22.
    """
    api_key = os.getenv("IONQ_API_KEY")
    if not api_key:
        print("[IonQ] IONQ_API_KEY not set — skipping", file=sys.stderr)
        return []

    headers = {"Authorization": f"apiKey {api_key}"}
    ts = datetime.now(timezone.utc).isoformat()
    try:
        resp = requests.get("https://api.ionq.co/v0.3/backends", headers=headers, timeout=15)
        resp.raise_for_status()
        backends = resp.json()
    except Exception as e:
        print(f"[IonQ] Failed to fetch backends: {e}", file=sys.stderr)
        return []

    rows = []
    for b in backends:
        char_url = b.get("characterization_url")
        fidelity, timing = {}, {}
        if char_url:
            try:
                char_resp = requests.get(f"https://api.ionq.co/v0.3{char_url}", headers=headers, timeout=15)
                if char_resp.status_code == 200:
                    char = char_resp.json()
                    fidelity = char.get("fidelity", {}) or {}
                    timing = char.get("timing", {}) or {}
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
    print(f"[IonQ] Collected {len(rows)} backends")
    return rows


def collect() -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting quantum-verifier cloud snapshot...")
    ibm_device_rows, qubit_rows, pair_rows = collect_ibm()
    ionq_device_rows = collect_ionq()

    _append_csv(DEVICE_CSV, DEVICE_FIELDS, ibm_device_rows + ionq_device_rows)
    _append_csv(QUBIT_CSV, QUBIT_FIELDS, qubit_rows)
    _append_csv(PAIR_CSV, PAIR_FIELDS, pair_rows)

    print(f"Saved {len(ibm_device_rows)} IBM + {len(ionq_device_rows)} IonQ device rows, "
          f"{len(qubit_rows)} qubit rows, {len(pair_rows)} pair rows.")


if __name__ == "__main__":
    collect()
