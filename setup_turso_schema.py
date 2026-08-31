"""
setup_turso_schema.py
----------------------
One-time: creates the device_snapshots / qubit_snapshots / pair_snapshots
tables in the shared Turso database (same schema as ibm_history.db), then
migrates the existing local history into it.

This is the shared, always-on source both quantum-verifier and
quantum-hardware-mcp read from and write to going forward -- added
2026-08-30 to close the "laptop must be on" gap: local SQLite files only
ever reflect whoever last ran an import on their own machine; this table
is reachable live, from anywhere, at query time, with no sync step.

Uses core/turso.py's plain requests-based client, not libsql_client -- see
that module's docstring for why (a confirmed hang-on-exit bug in
libsql_client's sync wrapper, found while wiring get_alerts() to this).

Run manually, once (safe to re-run -- skips rows already present):
    .venv/bin/python setup_turso_schema.py
"""
import csv
import os
import sqlite3

from dotenv import load_dotenv
load_dotenv()

from core.turso import execute, execute_batch, is_configured

DEVICE_CSV = os.path.join(os.path.dirname(__file__), "data", "device_snapshots.csv")
LOCAL_DB = os.path.join(os.path.dirname(__file__), "ibm_history.db")

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS device_snapshots (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        ts                 TEXT NOT NULL,
        provider           TEXT NOT NULL DEFAULT 'ibm',
        name               TEXT NOT NULL,
        num_qubits         INTEGER,
        operational        INTEGER,
        pending_jobs       INTEGER,
        avg_cx_error       REAL,
        avg_readout_error  REAL,
        median_t1_us       REAL,
        median_t2_us       REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_device_snapshots_name_ts ON device_snapshots(name, ts)",
    """
    CREATE TABLE IF NOT EXISTS qubit_snapshots (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        device_name        TEXT NOT NULL,
        qubit_index        INTEGER NOT NULL,
        property_name      TEXT NOT NULL,
        value              REAL,
        unit               TEXT,
        vendor_measured_at TEXT NOT NULL,
        polled_at          TEXT NOT NULL,
        UNIQUE(device_name, qubit_index, property_name, vendor_measured_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pair_snapshots (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        device_name        TEXT NOT NULL,
        qubit1             INTEGER NOT NULL,
        qubit2             INTEGER NOT NULL,
        gate_name          TEXT NOT NULL,
        property_name      TEXT NOT NULL,
        value              REAL,
        unit               TEXT,
        vendor_measured_at TEXT NOT NULL,
        polled_at          TEXT NOT NULL,
        UNIQUE(device_name, qubit1, qubit2, gate_name, property_name, vendor_measured_at)
    )
    """,
]

BATCH = 500


def _load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    if not is_configured():
        print("TURSO_DATABASE_URL / TURSO_AUTH_TOKEN not set in .env")
        return

    print("Creating schema...")
    for stmt in SCHEMA:
        execute(stmt)

    existing_device = set((r[0], r[1]) for r in execute("SELECT ts, name FROM device_snapshots"))
    print(f"Turso already has {len(existing_device)} device_snapshots rows.")

    device_rows = _load_csv(DEVICE_CSV)
    to_insert = [
        (r["ts"], r.get("provider") or "ibm", r["name"],
         int(r["num_qubits"]) if r.get("num_qubits") else None,
         int(float(r["operational"])) if r.get("operational") not in (None, "") else None,
         int(r["pending_jobs"]) if r.get("pending_jobs") else None,
         float(r["avg_cx_error"]) if r.get("avg_cx_error") else None,
         float(r["avg_readout_error"]) if r.get("avg_readout_error") else None,
         float(r["avg_t1_us"]) if r.get("avg_t1_us") else None,
         float(r["avg_t2_us"]) if r.get("avg_t2_us") else None)
        for r in device_rows if (r["ts"], r["name"]) not in existing_device
    ]
    print(f"Migrating {len(to_insert)} new device_snapshots rows...")
    statements = [
        ("INSERT INTO device_snapshots (ts, provider, name, num_qubits, operational, "
         "pending_jobs, avg_cx_error, avg_readout_error, median_t1_us, median_t2_us) "
         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
        for row in to_insert
    ]
    for i in range(0, len(statements), BATCH):
        execute_batch(statements[i:i + BATCH])
        print(f"  {min(i + BATCH, len(statements))}/{len(statements)}")

    total = execute("SELECT COUNT(*) FROM device_snapshots")[0][0]
    earliest, latest = execute("SELECT MIN(ts), MAX(ts) FROM device_snapshots")[0]
    print(f"device_snapshots in Turso: {total} rows, {earliest} -> {latest}")

    # qubit_snapshots / pair_snapshots: migrate from the LOCAL db, not the
    # CSV -- ibm_history.db has the full accumulated history since
    # 2026-08-24, the CSV only has one run's worth at a time.
    if os.path.exists(LOCAL_DB):
        local = sqlite3.connect(LOCAL_DB)

        for table, cols in [
            ("qubit_snapshots",
             "device_name, qubit_index, property_name, value, unit, vendor_measured_at, polled_at"),
            ("pair_snapshots",
             "device_name, qubit1, qubit2, gate_name, property_name, value, unit, vendor_measured_at, polled_at"),
        ]:
            rows = local.execute(f"SELECT {cols} FROM {table}").fetchall()
            print(f"Migrating {len(rows)} {table} rows from local db...")
            placeholders = ", ".join("?" for _ in cols.split(", "))
            statements = [(f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})", row)
                          for row in rows]
            for i in range(0, len(statements), BATCH):
                execute_batch(statements[i:i + BATCH])
                print(f"  {table}: {min(i + BATCH, len(statements))}/{len(statements)}")

        local.close()
        for table in ("qubit_snapshots", "pair_snapshots"):
            total = execute(f"SELECT COUNT(*) FROM {table}")[0][0]
            print(f"{table} in Turso: {total} rows")


if __name__ == "__main__":
    main()
