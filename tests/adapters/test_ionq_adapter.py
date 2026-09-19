"""
Shape-parity tests: get_adapter("ionq") must return exactly what calling
providers.ionq's functions directly returns — the adapter is a pure
pass-through at this stage, nothing more.
"""
import os
import pytest
from dotenv import load_dotenv

load_dotenv()

IONQ_KEY_PRESENT = bool(os.getenv("IONQ_API_KEY"))
pytestmark = pytest.mark.skipif(
    not IONQ_KEY_PRESENT, reason="IONQ_API_KEY not set — skipping live IonQ adapter tests"
)

import providers.ionq as ionq
from adapters.base import AdapterCapabilityError
from adapters.registry import get_adapter


def test_list_devices_matches_direct_call():
    adapter = get_adapter("ionq")
    assert adapter.list_devices() == ionq.ionq_devices()


def test_get_device_details_matches_direct_call():
    adapter = get_adapter("ionq")
    devices = adapter.list_devices()
    name = devices[0]["name"]
    assert adapter.get_device_details(name) == ionq.get_device_details(name)


def test_capability_flags():
    adapter = get_adapter("ionq")
    assert adapter.supports_estimate_gates is True
    assert adapter.supports_estimate_cost is True
    assert adapter.supports_preflight_check is True
    assert adapter.supports_cancel_job is False
    assert adapter.supports_list_jobs is False
    assert adapter.has_topology_risk is False


def test_unsupported_capabilities_raise_clearly():
    adapter = get_adapter("ionq")
    with pytest.raises(AdapterCapabilityError):
        adapter.cancel_job("some-job-id")
    with pytest.raises(AdapterCapabilityError):
        adapter.list_jobs()
    with pytest.raises(AdapterCapabilityError):
        adapter.cross_check_fidelity(None, {})


def test_check_topology_is_a_documented_noop():
    adapter = get_adapter("ionq")
    result = adapter.check_topology(circuit=None)
    assert result == {
        "applicable": False, "passed": True,
        "note": "IonQ is all-to-all connected — no routing/degree risk exists for this provider.",
    }


BELL_QASM = (
    'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\n'
    "h q[0];\ncx q[0],q[1];\nmeasure q[0] -> c[0];\nmeasure q[1] -> c[1];\n"
)


def test_job_status_matches_direct_call_for_a_nonexistent_job():
    # Two separate live 404 responses embed a live response timestamp/id in
    # the error text, so compare structurally (both errored, same shape),
    # not by exact string equality of the error message.
    adapter = get_adapter("ionq")
    fake_id = "00000000-0000-0000-0000-000000000000"
    a = adapter.job_status(fake_id)
    b = ionq.ionq_job_status(fake_id, "ionq_simulator")
    assert a.keys() == b.keys() == {"error"}
    assert "Not Found" in a["error"] and "Not Found" in b["error"]


def test_job_results_matches_direct_call_for_a_nonexistent_job():
    adapter = get_adapter("ionq")
    fake_id = "00000000-0000-0000-0000-000000000000"
    a = adapter.job_results(fake_id)
    b = ionq.ionq_job_results(fake_id, "simulator")
    assert a.keys() == b.keys() == {"error"}
    assert "Not Found" in a["error"] and "Not Found" in b["error"]


def test_estimate_gates_matches_direct_call():
    adapter = get_adapter("ionq")
    assert adapter.estimate_gates(BELL_QASM, "forte-1") == ionq.estimate_ionq_gates(BELL_QASM, "forte-1")


def test_estimate_cost_matches_direct_call():
    adapter = get_adapter("ionq")
    assert adapter.estimate_cost([BELL_QASM]) == ionq.estimate_ionq_cost([BELL_QASM])
