"""
Correctness tests for PennyLaneAdapter -- not shape-parity (there's no
legacy providers/*.py function this is a pass-through of, unlike IBM/IonQ),
against known-exact circuits instead. Gated by package availability, not
API keys, since PennyLane runs fully locally.
"""
import pytest

pytest.importorskip("pennylane")

from adapters.registry import get_adapter
from qiskit import QuantumCircuit

BELL_QASM = ('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\n'
             'h q[0];\ncx q[0],q[1];\nmeasure q[0]->c[0];\nmeasure q[1]->c[1];\n')

# Deliberately asymmetric: X only on qubit 0. Qiskit's convention puts the
# HIGHEST-index clbit leftmost, so this must produce EXACTLY "01", never
# "10" -- confirmed empirically these are genuinely reversed raw
# conventions (PennyLane's raw sample columns give [1, 0] for this circuit;
# Qiskit's get_counts() gives "01"). This is the concrete test for the
# bit-order reversal in adapters/pennylane_adapter.py's
# _counts_from_samples.
ASYMMETRIC_QASM = ('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\n'
                    'x q[0];\nmeasure q[0]->c[0];\nmeasure q[1]->c[1];\n')


def test_check_topology_is_a_documented_noop():
    adapter = get_adapter("pennylane")
    assert adapter.check_topology(circuit=None)["applicable"] is False


def test_asymmetric_circuit_matches_qiskit_bit_order_exactly():
    adapter = get_adapter("pennylane")
    circuit = QuantumCircuit.from_qasm_str(ASYMMETRIC_QASM)
    hw = adapter.simulate_hardware_aware(circuit, "default.qubit", shots=256)
    assert set(hw["counts"].keys()) == {"01"}  # deterministic, no tolerance needed


def test_bell_state_matches_expected_distribution_within_statistical_tolerance():
    adapter = get_adapter("pennylane")
    circuit = QuantumCircuit.from_qasm_str(BELL_QASM)
    shots = 4096
    hw = adapter.simulate_hardware_aware(circuit, "default.qubit", shots=shots)
    p00 = hw["counts"].get("00", 0) / shots
    assert 0.4 < p00 < 0.6  # noiseless Bell state, statistical tolerance only
    assert set(hw["counts"].keys()) <= {"00", "11"}  # no leakage into 01/10


def test_gate_synthesis_check_present_and_passes():
    adapter = get_adapter("pennylane")
    circuit = QuantumCircuit.from_qasm_str(BELL_QASM)
    hw = adapter.simulate_hardware_aware(circuit, "default.qubit", shots=256)
    assert "gate_synthesis_check" in hw  # keeps verify()'s BLOCK gate honest
    assert hw["gate_synthesis_check"]["passed"] is True


def test_submit_job_is_synchronous_and_status_is_immediately_done():
    adapter = get_adapter("pennylane")
    sub = adapter.submit_job("default.qubit", [BELL_QASM], shots=256)
    assert sub["status"] == "DONE"
    assert sub["is_real_hardware"] is False
    status = adapter.job_status(sub["job_id"])
    assert status["status"] == "DONE"
    results = adapter.job_results(sub["job_id"])
    assert "counts" in results
    assert sum(results["counts"].values()) == 256


def test_job_results_for_unknown_job_id_reports_a_clear_error():
    adapter = get_adapter("pennylane")
    assert "error" in adapter.job_results("not-a-real-job-id")
