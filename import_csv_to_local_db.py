"""
import_csv_to_local_db.py
--------------------------
Closes the real gap found 2026-08-30: the deep device history now sitting
in data/device_snapshots.csv (2022 -> today, cloud-collected every 2h) was
being collected but never actually READ by anything -- get_alerts()'s
drift detection (providers/ibm.py::_recent_drift_alert) queries
ibm_history.db's own device_snapshots table, a completely separate,
shallower local database (laptop-only, ~240 rows from 2026-08-24 onward).
Collecting good data and a tool actually using it are two different jobs;
only the first one was done before this script.

This is a one-time (re-runnable, idempotent) import: copies every row from
data/device_snapshots.csv into ibm_history.db's device_snapshots table,
skipping rows that already exist there (matched on ts + name, since that
table has no UNIQUE constraint of its own to rely on). After this,
get_alerts() and anything else querying ibm_history.db can see the real
2022-onward history, not just the last few days.

Run manually, or re-run any time after a fresh backfill/collection:
    .venv/bin/python import_csv_to_local_db.py
"""
import csv
import os
import sqlite3

from providers.ibm import DB_PATH

CSV_PATH = os.path.join(os.path.dirname(__file__), "data", "device_snapshots.csv")


def main() -> None:
    if not os.path.exists(CSV_PATH):
        print(f"CSV not found: {CSV_PATH}")
        return

    with open(CSV_PATH, newline="") as f:
        csv_rows = list(csv.DictReader(f))
    print(f"CSV has {len(csv_rows)} rows.")

    con = sqlite3.connect(DB_PATH)
    existing = set(con.execute("SELECT ts, name FROM device_snapshots").fetchall())
    print(f"ibm_history.db already has {len(existing)} rows.")

    to_insert = []
    for r in csv_rows:
        key = (r["ts"], r["name"])
        if key in existing:
            continue
        to_insert.append((
            r["ts"], r["name"],
            int(r["num_qubits"]) if r.get("num_qubits") else None,
            int(float(r["operational"])) if r.get("operational") not in (None, "") else None,
            int(r["pending_jobs"]) if r.get("pending_jobs") else None,
            float(r["avg_cx_error"]) if r.get("avg_cx_error") else None,
            float(r["avg_readout_error"]) if r.get("avg_readout_error") else None,
            float(r["avg_t1_us"]) if r.get("avg_t1_us") else None,
            float(r["avg_t2_us"]) if r.get("avg_t2_us") else None,
            r.get("provider") or "ibm",
        ))
        existing.add(key)

    con.executemany(
        """
        INSERT INTO device_snapshots
            (ts, name, num_qubits, operational, pending_jobs,
             avg_cx_error, avg_readout_error, median_t1_us, median_t2_us, provider)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        to_insert,
    )
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM device_snapshots").fetchone()[0]
    earliest, latest = con.execute("SELECT MIN(ts), MAX(ts) FROM device_snapshots").fetchone()
    con.close()

    print(f"Inserted {len(to_insert)} new rows. ibm_history.db's device_snapshots now has "
          f"{total} rows total, spanning {earliest} -> {latest}.")
    print("get_alerts() / drift detection now sees the real history, not just the last few days.")


if __name__ == "__main__":
    main()
