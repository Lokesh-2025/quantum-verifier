"""
backfill_from_hardware_mcp.py
------------------------------
One-time backfill, run 2026-08-30 and REDONE the same day against a
deeper source after the first pass was found to be too shallow.

First version pulled from quantum-hardware-mcp's data/snapshots.csv
(GitHub Actions collector, running since only 2026-06-11, IBM-only, no
T1/T2). This version pulls from quantum-hardware-mcp's devices.db instead
-- the same repo's deeper local archive, built from dedicated backfill
scripts (backfill_ibm.py, backfill_gap.py, and an IonQ equivalent) that
pulled each vendor's own historical calibration API back to when each
device came online:

    IonQ qpu.harmony     -> 2022-01-07
    IonQ qpu.forte-1     -> 2022-01-13
    IonQ qpu.aria-1/2    -> 2023
    IBM  ibm_fez/marrakesh -> 2024-10-01 (IBM's API 404s before this)
    IBM  ibm_kingston    -> 2025-03-16

Scoped honestly, not "everything":
- ONLY provider in ('ibm', 'ionq'), matching quantum-verifier's own
  cloud_snapshot.py scope. The devices.db table also holds 'braket/*'
  rows (IonQ/Amazon/QuEra/Xanadu devices reached via AWS Braket instead
  of each vendor's native API) -- these are a DIFFERENT access path to
  overlapping hardware, only 2 months deep, and outside what this repo
  collects going forward, so they're deliberately excluded here rather
  than silently merged in as if they were the same lineage of data.
- 'simulator' rows excluded -- not a real device.
- Still no per-qubit/per-pair backfill possible -- devices.db only ever
  stored device-level aggregates, same as the CSV. qubit_snapshots.csv /
  pair_snapshots.csv genuinely start from whenever this repo's own
  collector first ran; there is no deeper source for that anywhere.

Run manually, once:
    .venv/bin/python backfill_from_hardware_mcp.py
"""
import csv
import os
import sqlite3

SOURCE_DB = os.path.expanduser("~/quantum-hardware-mcp/devices.db")
DEST = os.path.join(os.path.dirname(__file__), "data", "device_snapshots.csv")

DEST_FIELDS = ["ts", "provider", "name", "num_qubits", "operational",
               "pending_jobs", "avg_cx_error", "avg_readout_error",
               "avg_t1_us", "avg_t2_us"]


def main() -> None:
    if not os.path.exists(SOURCE_DB):
        print(f"Source not found: {SOURCE_DB}")
        return

    con = sqlite3.connect(SOURCE_DB)
    cur = con.execute(
        """
        SELECT ts, provider, name, num_qubits, operational, pending_jobs,
               avg_cx_error, avg_readout_error, median_t1_us, median_t2_us
        FROM device_snapshots
        WHERE provider IN ('ibm', 'ionq') AND name != 'simulator'
        ORDER BY ts
        """
    )
    source_rows = cur.fetchall()
    con.close()
    print(f"Source (devices.db) has {len(source_rows)} real ibm/ionq device rows "
          f"(braket/* and simulator rows excluded).")

    # devices.db is itself current as of yesterday (hardware-mcp's own
    # collectors keep it live), so it already comprehensively supersedes
    # the previous shallow CSV-based backfill -- this is a full rebuild,
    # not a merge, to avoid two overlapping copies of the same history
    # under slightly different timestamps.
    all_rows = [{
        "ts": ts, "provider": provider, "name": name,
        "num_qubits": num_qubits, "operational": operational,
        "pending_jobs": pending_jobs, "avg_cx_error": avg_cx_error,
        "avg_readout_error": avg_readout_error,
        "avg_t1_us": t1, "avg_t2_us": t2,
    } for (ts, provider, name, num_qubits, operational, pending_jobs,
           avg_cx_error, avg_readout_error, t1, t2) in source_rows]

    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    with open(DEST, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DEST_FIELDS)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k) for k in DEST_FIELDS})

    print(f"Rebuilt {DEST} with {len(all_rows)} rows, spanning "
          f"{all_rows[0]['ts']} -> {all_rows[-1]['ts']}.")
    print("Note: qubit-level/pair-level data still NOT backfilled -- devices.db "
          "never stored that granularity either, at any point in its history.")
    print("This repo's own 2h cloud collector will keep appending fresh rows "
          "going forward -- re-run cloud_snapshot.py or wait for the next cron tick.")


if __name__ == "__main__":
    main()
