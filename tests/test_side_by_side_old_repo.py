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

load_dotenv()

IONQ_KEY_PRESENT = bool(os.getenv("IONQ_API_KEY"))
pytestmark = pytest.mark.skipif(
    not IONQ_KEY_PRESENT, reason="IONQ_API_KEY not set — skipping live side-by-side tests"
)

OLD_REPO = os.path.expanduser("~/quantum-hardware-mcp")


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
    """Pure function over gate-count math — no live API call, must match exactly."""
    old = _load_old_server()
    from providers.ionq import estimate_ionq_cost as new_estimate_ionq_cost

    old_result = json.loads(old.estimate_ionq_cost([BELL], shots=4096))
    new_result = new_estimate_ionq_cost([BELL], shots=4096)

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
