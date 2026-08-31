"""
backfill_from_hardware_mcp.py
------------------------------
One-time backfill, run once on 2026-08-30: pulls the real, historical IBM
device-level rows out of quantum-hardware-mcp's data/snapshots.csv (its
GitHub Actions collector has been running since 2026-06-11) into
quantum-verifier's own data/device_snapshots.csv.

Scoped honestly, not "everything":
- ONLY device-level rows (ts, name, num_qubits, operational, pending_jobs,
  avg_cx_error, avg_readout_error) -- that source CSV has no T1/T2 and no
  per-qubit/per-pair detail at any point in its history, so there is
  nothing to backfill for quantum-verifier's qubit_snapshots.csv /
  pair_snapshots.csv -- those start fresh from whenever THIS repo's own
  collector first ran, real gap included, not filled in retroactively.
- ONLY real per-device rows (name starting with the actual backend prefix,
  e.g. "ibm_fez") -- the source CSV also has provider-summary rows (name
  literally "ibm", "ionq", etc.) mixed in, which are not device snapshots
  and are skipped here.

Run manually, once:
    .venv/bin/python backfill_from_hardware_mcp.py
"""
import csv
import os

SOURCE = os.path.expanduser("~/quantum-hardware-mcp/data/snapshots.csv")
DEST = os.path.join(os.path.dirname(__file__), "data", "device_snapshots.csv")

DEST_FIELDS = ["ts", "provider", "name", "num_qubits", "operational",
               "pending_jobs", "avg_cx_error", "avg_readout_error",
               "avg_t1_us", "avg_t2_us"]


def main() -> None:
    if not os.path.exists(SOURCE):
        print(f"Source not found: {SOURCE}")
        return

    with open(SOURCE, newline="") as f:
        rows = list(csv.DictReader(f))

    ibm_rows = [r for r in rows if r.get("name", "").startswith("ibm_")]
    print(f"Source has {len(rows)} total rows, {len(ibm_rows)} real IBM device rows.")

    # Load whatever's already there (today's live smoke-test rows) so the
    # final file is one clean chronologically-sorted merge, not backfilled
    # history appended after today's rows.
    existing_rows = []
    existing_keys = set()
    dest_exists = os.path.exists(DEST)
    if dest_exists:
        with open(DEST, newline="") as f:
            for r in csv.DictReader(f):
                existing_rows.append(r)
                existing_keys.add((r["ts"], r["name"]))

    new_rows = []
    for r in ibm_rows:
        key = (r["ts"], r["name"])
        if key in existing_keys:
            continue
        new_rows.append({
            "ts": r["ts"], "provider": "ibm", "name": r["name"],
            "num_qubits": r.get("num_qubits") or None,
            "operational": r.get("operational") or None,
            "pending_jobs": r.get("pending_jobs") or None,
            "avg_cx_error": r.get("avg_cx_error") or None,
            "avg_readout_error": r.get("avg_readout_error") or None,
            "avg_t1_us": None,   # not present in the source at any point
            "avg_t2_us": None,   # not present in the source at any point
        })
        existing_keys.add(key)

    all_rows = sorted(existing_rows + new_rows, key=lambda r: r["ts"])

    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    with open(DEST, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DEST_FIELDS)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k) for k in DEST_FIELDS})

    written = len(new_rows)
    print(f"Backfilled {written} new IBM device-level rows into {DEST}")
    print("Note: T1/T2 left blank -- never collected in the source history.")
    print("Note: qubit-level/pair-level data NOT backfilled -- no source data ever existed for it.")


if __name__ == "__main__":
    main()
