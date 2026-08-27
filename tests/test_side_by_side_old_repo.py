"""
Side-by-side diff against the old repo (Phase 1 plan item #3, never actually
run when Phase 1 shipped). Confirms the copied functions in providers/ibm.py
and providers/ionq.py didn't silently drift from the untouched originals in
~/quantum-hardware-mcp/server.py.

Read-only: imports the old repo's server.py to call its functions, never
writes to it. Deterministic, non-live-money functions get exact-match
assertions; live device listings compare names/counts only, since fields
like queue depth legitimately change moment to moment.
"""
import importlib.util
import json
import os
import sys

import pytest
from dotenv import load_dotenv

import providers.ibm as ibm

load_dotenv()

IONQ_KEY_PRESENT = bool(os.getenv("IONQ_API_KEY"))
pytestmark = pytest.mark.skipif(
    not IONQ_KEY_PRESENT, reason="IONQ_API_KEY not set — skipping live side-by-side tests"
)

OLD_REPO = os.path.expanduser("~/quantum-hardware-mcp")


@pytest.fixture(autouse=True)
def _isolate_ibm_history_db(tmp_path, monkeypatch):
    """test_list_devices_matches_old_repo_structurally calls the real,
    live IBM API and writes real calibration snapshots to ibm_history.db as
    a side effect. Isolate that write, mirroring the fix already used in
    test_calibration_auditor.py / test_chip_identity.py / test_drift_gate.py /
    test_reproducibility_qubit_selection.py. The old repo's own copy of
    list_devices (loaded separately below via _load_old_server) is untouched
    by this -- it writes nowhere, this file is read-only against it."""
    db_path = str(tmp_path / "test_ibm_history.db")
    monkeypatch.setattr(ibm, "DB_PATH", db_path)
    ibm._init_db()


def _load_old_server():
    """Read-only import of the untouched old repo's server.py."""
    if not os.path.isdir(OLD_REPO):
        pytest.skip(f"old repo not found at {OLD_REPO}")
    sys.path.insert(0, OLD_REPO)
    spec = importlib.util.spec_from_file_location("old_server", os.path.join(OLD_REPO, "server.py"))
    old_server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old_server)
    return old_server


BELL = (
    'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\n'
    "h q[0];\ncx q[0],q[1];\nmeasure q[0]->c[0];\nmeasure q[1]->c[1];\n"
)


def test_estimate_ionq_gates_matches_old_repo():
    """Pure function, deterministic given the same circuit/backend/opt-level
    — must produce identical native gate counts to the untouched original."""
    old = _load_old_server()
    from providers.ionq import estimate_ionq_gates as new_estimate_ionq_gates

    old_result = json.loads(old.estimate_ionq_gates(BELL, backend_name="forte-1", optimization_level=1))
    new_result = new_estimate_ionq_gates(BELL, backend_name="forte-1", optimization_level=1)

    assert old_result["native_gate_counts"] == new_result["native_gate_counts"]
    assert old_result["two_qubit_gates"] == new_result["two_qubit_gates"]
    assert old_result["native_2q_gate_family"] == new_result["native_2q_gate_family"]


def test_estimate_ionq_cost_matches_old_repo():
    """Pure function over gate-count math. The old repo reports a single
    estimated_total_usd point; the new one deliberately reports a
    [low, high] range instead — two real data points on this project's own
    hardware imply per-gate rates 2.65x apart, so a single fake-precise
    number would be less honest (see providers/ionq.py::estimate_ionq_cost
    docstring). Not a schema regression: confirm the old point estimate
    falls within the new range, and every other field still matches
    exactly."""
    old = _load_old_server()
    from providers.ionq import estimate_ionq_cost as new_estimate_ionq_cost

    old_result = json.loads(old.estimate_ionq_cost([BELL], shots=4096))
    new_result = new_estimate_ionq_cost([BELL], shots=4096)

    old_cost = old_result.pop("estimated_total_usd")
    new_low = new_result.pop("estimated_total_usd_low")
    new_high = new_result.pop("estimated_total_usd_high")
    assert new_low <= old_cost <= new_high
    assert old_result == new_result


def test_ionq_devices_matches_old_repo_structurally():
    """Both hit the same live IonQ account — device names/count should match
    exactly; live-changing fields (queue depth, status at this instant) are
    deliberately excluded from the comparison."""
    old = _load_old_server()
    from providers.ionq import ionq_devices as new_ionq_devices

    old_result = json.loads(old.ionq_devices())
    new_result = new_ionq_devices()

    old_names = sorted(d["name"] for d in old_result)
    new_names = sorted(d["name"] for d in new_result)
    assert old_names == new_names, (old_names, new_names)


def test_list_devices_matches_old_repo_structurally():
    """Same idea for IBM — both hit the same live IBM account."""
    old = _load_old_server()
    from providers.ibm import list_devices as new_list_devices

    old_result = json.loads(old.list_devices())
    new_result = new_list_devices()

    old_names = sorted(d["name"] for d in old_result)
    new_names = sorted(d["name"] for d in new_result)
    assert old_names == new_names, (old_names, new_names)
