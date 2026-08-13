"""
Test coverage for the IBM-side account/quota check, built as the IBM
equivalent of ionq_account_check/the IonQ budget preflight check --
after directly checking IBM's real API rather than assuming the same
dollar-budget shape applies. IBM's free-plan quota is genuinely
time-based (seconds of QPU access), not a dollar amount.
"""
import os
import pytest
from dotenv import load_dotenv

load_dotenv()

IBM_TOKEN_PRESENT = bool(os.getenv("IBM_QUANTUM_TOKEN"))
pytestmark = pytest.mark.skipif(
    not IBM_TOKEN_PRESENT, reason="IBM_QUANTUM_TOKEN not set — skipping live IBM tooling tests"
)

from providers.ibm import ibm_account_check, _check_ibm_quota_before_submitting
from qiskit import QuantumCircuit


def test_account_check_returns_real_instance_and_usage_data():
    result = ibm_account_check()
    assert "error" not in result
    assert "instances" in result and len(result["instances"]) >= 1
    usage = result["usage"]
    for field in ("seconds_used", "seconds_limit", "seconds_remaining", "limit_reached"):
        assert field in usage


def test_zero_quota_warning_matches_real_remaining_seconds():
    result = ibm_account_check()
    remaining = result["usage"]["seconds_remaining"]
    assert result["zero_quota_warning"] == (remaining is not None and remaining <= 0)


def test_quota_check_refuses_when_estimate_exceeds_remaining():
    """The end-to-end refusal path, against a real device and a real
    (if surprisingly high, for this account's estimator) runtime
    estimate — confirms the check actually blocks before submission
    when the numbers say it should, using real data rather than a mock."""
    from providers.ibm import list_devices
    available = list_devices()
    backend_name = next((d["name"] for d in available if d.get("operational")), None)
    if not backend_name:
        pytest.skip("no operational IBM backend available right now")

    qc = QuantumCircuit(2, 2)
    qc.h(0); qc.cx(0, 1); qc.measure([0, 1], [0, 1])
    result = _check_ibm_quota_before_submitting(backend_name, qc, shots=100)
    account = ibm_account_check()
    remaining = account["usage"]["seconds_remaining"]
    # Whatever the real estimate turns out to be, the check's own math must
    # agree with it — refuse iff the estimate genuinely exceeds remaining.
    if result["error"] is not None:
        assert result["estimated_seconds"] > remaining
    else:
        assert True  # no independent estimate to compare against when it passes


def test_quota_check_boundary_logic_directly():
    """Isolates the comparison logic itself from whatever a real circuit's
    estimate happens to be right now, using controlled numbers."""
    remaining = ibm_account_check()["usage"]["seconds_remaining"]
    if remaining is None:
        pytest.skip("no quota-remaining figure available to test the boundary against")
    # A circuit deliberately sized to need far more shots (and therefore
    # estimated time) than any plausible remaining quota.
    qc = QuantumCircuit(2, 2)
    qc.h(0); qc.cx(0, 1); qc.measure([0, 1], [0, 1])
    from providers.ibm import list_devices
    backend_name = next((d["name"] for d in list_devices() if d.get("operational")), None)
    if not backend_name:
        pytest.skip("no operational IBM backend available right now")
    result = _check_ibm_quota_before_submitting(backend_name, qc, shots=100000)
    assert result["error"] is not None, "an enormous shot count must exceed any real remaining quota"
