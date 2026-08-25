"""
Recurring calibration snapshot collector for quantum-verifier's own local
history (ibm_history.db). Added 2026-08-24 — this project previously had
no automated collection at all (a deliberate original scope decision, see
providers/ibm.py's file-header comment), so its history only grew when
someone happened to call list_devices()/get_device_details() by hand: 72
rows across 8 days before this fix.

IBM only, matching this project's existing scope (ibm_history.db has no
IonQ equivalent table or collector — providers/ionq.py has its own
separate, real-money safety checks but no historical archive).

Run manually:
    .venv/bin/python snapshot.py

Or let the LaunchAgent (com.quantum-verifier.snapshot) call it automatically.
"""
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from providers.ibm import list_devices, get_device_details


def collect() -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting quantum-verifier snapshot...")
    try:
        devices = list_devices()
    except Exception as e:
        print(f"ERROR: list_devices() failed: {e}", file=sys.stderr)
        sys.exit(1)

    n_ok, n_failed = 0, 0
    for d in devices:
        name = d.get("name")
        if not name:
            continue
        try:
            get_device_details(name)  # saves real calibration data via _save_snapshots
            n_ok += 1
        except Exception as e:
            n_failed += 1
            print(f"  [{name}] get_device_details failed: {e}", file=sys.stderr)

    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"Saved calibration snapshots for {n_ok} device(s)"
          + (f", {n_failed} failed" if n_failed else ""))


if __name__ == "__main__":
    collect()
