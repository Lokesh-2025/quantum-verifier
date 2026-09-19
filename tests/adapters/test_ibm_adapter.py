"""
Shape-parity tests: get_adapter("ibm") must return exactly what calling
providers.ibm's functions directly returns — the adapter is a pure
pass-through at this stage, nothing more.
"""
import os
import pytest
from dotenv import load_dotenv

load_dotenv()

IBM_TOKEN_PRESENT = bool(os.getenv("IBM_QUANTUM_TOKEN"))
pytestmark = pytest.mark.skipif(
    not IBM_TOKEN_PRESENT, reason="IBM_QUANTUM_TOKEN not set — skipping live IBM adapter tests"
)

import providers.ibm as ibm
from adapters.base import AdapterCapabilityError
from adapters.registry import get_adapter


@pytest.fixture(autouse=True)
def _isolate_ibm_history_db(tmp_path, monkeypatch):
    """list_devices writes real calibration snapshots as a side effect —
    isolate that write, same fix as tests/test_ibm_tooling.py."""
    db_path = str(tmp_path / "test_ibm_history.db")
    monkeypatch.setattr(ibm, "DB_PATH", db_path)
    ibm._init_db()


def test_list_devices_matches_direct_call():
    # pending_jobs is genuinely live and can change between these two
    # separate calls a fraction of a second apart -- compare everything
    # except that one field to avoid rare, real-world-state-driven flakes.
    adapter = get_adapter("ibm")
    a = adapter.list_devices()
    b = ibm.list_devices()
    assert len(a) == len(b)
    for da, db in zip(a, b):
        assert {k: v for k, v in da.items() if k != "pending_jobs"} == \
               {k: v for k, v in db.items() if k != "pending_jobs"}


def test_get_device_details_matches_direct_call():
    adapter = get_adapter("ibm")
    devices = adapter.list_devices()
    name = devices[0]["name"]
    a = adapter.get_device_details(name)
    b = ibm.get_device_details(name)
    assert {k: v for k, v in a.items() if k != "pending_jobs"} == \
           {k: v for k, v in b.items() if k != "pending_jobs"}


def test_capability_flags():
    adapter = get_adapter("ibm")
    assert adapter.supports_cancel_job is True
    assert adapter.supports_list_jobs is True
    assert adapter.supports_fidelity_cross_check is True
    assert adapter.supports_estimate_gates is False
    assert adapter.supports_estimate_cost is False
    assert adapter.has_topology_risk is True


def test_unsupported_capabilities_raise_clearly():
    adapter = get_adapter("ibm")
    with pytest.raises(AdapterCapabilityError):
        adapter.estimate_gates("qasm", "backend")
    with pytest.raises(AdapterCapabilityError):
        adapter.estimate_cost(["qasm"])
    with pytest.raises(AdapterCapabilityError):
        adapter.preflight_check(["qasm"], "backend")


def test_job_status_matches_direct_call_for_a_nonexistent_job():
    adapter = get_adapter("ibm")
    fake_id = "d0000000000000000000000"
    assert adapter.job_status(fake_id) == ibm.job_status(fake_id)


def test_job_results_matches_direct_call_for_a_nonexistent_job():
    adapter = get_adapter("ibm")
    fake_id = "d0000000000000000000000"
    assert adapter.job_results(fake_id) == ibm.job_results(fake_id)


def test_cancel_job_matches_direct_call_for_a_nonexistent_job():
    adapter = get_adapter("ibm")
    fake_id = "d0000000000000000000000"
    assert adapter.cancel_job(fake_id) == ibm.cancel_job(fake_id)


def test_list_jobs_matches_direct_call():
    adapter = get_adapter("ibm")
    assert adapter.list_jobs(limit=3) == ibm.list_jobs(3)
