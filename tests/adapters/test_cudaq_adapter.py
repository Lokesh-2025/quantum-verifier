"""
Correctness tests for CudaQAdapter -- not shape-parity (no legacy
providers/*.py function to compare against), against known-exact circuits
instead. Gated by package availability, not API keys, since cudaq's CPU
target runs fully locally.
"""
import pytest

pytest.importorskip("cudaq")

from adapters.registry import get_adapter
from adapters.cudaq_adapter import select_target, run_circuit, get_exact_statevector
from qiskit import QuantumCircuit

BELL_QASM = ('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\n'
             'h q[0];\ncx q[0],q[1];\nmeasure q[0]->c[0];\nmeasure q[1]->c[1];\n')

# See tests/adapters/test_pennylane_adapter.py's identical asymmetric test
# for the same real motivation -- confirmed empirically cudaq puts qubit 0
# LEFTMOST in its raw bitstrings (X-only-on-qubit-0 gives cudaq "10"),
# the opposite of Qiskit's "01". Reversed in adapters/cudaq_adapter.py's
# _counts_from_sample_result to match.
ASYMMETRIC_QASM = ('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\n'
                    'x q[0];\nmeasure q[0]->c[0];\nmeasure q[1]->c[1];\n')


def test_target_selection_falls_back_to_cpu_on_this_machine():
    """
    Asserts 'qpp-cpu' -- true on this repo's dev machine (macOS, no NVIDIA
    GPU, and cudaq's macOS wheel doesn't ship the 'nvidia' target at all,
    confirmed directly: cudaq.set_target("nvidia") raises RuntimeError:
    "Invalid target name (nvidia)" on this platform). This assertion is
    expected to become FALSE and require updating if this test ever runs
    on real Linux+NVIDIA-GPU hardware -- that would be select_target()
    correctly picking 'nvidia', not a regression. Do not "fix" this test
    to pass unconditionally everywhere.
    """
    assert select_target() == "qpp-cpu"


def test_check_topology_is_a_documented_noop():
    adapter = get_adapter("cudaq")
    assert adapter.check_topology(circuit=None)["applicable"] is False


def test_asymmetric_circuit_matches_qiskit_bit_order_exactly():
    circuit = QuantumCircuit.from_qasm_str(ASYMMETRIC_QASM)
    counts = run_circuit(circuit, shots=256)
    assert set(counts.keys()) == {"01"}  # deterministic, no tolerance needed


def test_bell_state_matches_expected_distribution_within_statistical_tolerance():
    circuit = QuantumCircuit.from_qasm_str(BELL_QASM)
    shots = 4096
    counts = run_circuit(circuit, shots=shots)
    p00 = counts.get("00", 0) / shots
    assert 0.4 < p00 < 0.6
    assert set(counts.keys()) <= {"00", "11"}


def test_gate_synthesis_check_present_and_passes():
    adapter = get_adapter("cudaq")
    circuit = QuantumCircuit.from_qasm_str(BELL_QASM)
    hw = adapter.simulate_hardware_aware(circuit, "qpp-cpu", shots=256)
    assert "gate_synthesis_check" in hw
    assert hw["gate_synthesis_check"]["passed"] is True


def test_exact_statevector_matches_qiskit_statevector_exactly():
    """
    No bit-order reversal needed here -- confirmed empirically cudaq's
    get_state array ordering already matches
    qiskit.quantum_info.Statevector.from_instruction's exactly for the
    same circuit (unlike sampled bitstrings, which do need reversal).
    """
    import numpy as np
    from qiskit.quantum_info import Statevector

    circuit = QuantumCircuit(2)
    circuit.h(0)
    circuit.cx(0, 1)
    cudaq_sv = get_exact_statevector(circuit)
    qiskit_sv = Statevector.from_instruction(circuit)
    assert np.allclose(cudaq_sv.data, qiskit_sv.data, atol=1e-6)


def test_rzz_translation_produces_a_valid_result():
    """
    rzz has no direct cudaq kernel-builder method -- built from the
    standard exact CX-RZ-CX decomposition instead. This just confirms the
    translation runs without error and produces a real, valid count
    distribution, not a specific expected split (rzz is diagonal in the
    computational basis, so it changes phase, not measurement
    probabilities, for this particular circuit).
    """
    circuit = QuantumCircuit(2, 2)
    circuit.h(0)
    circuit.rzz(0.7, 0, 1)
    circuit.measure([0, 1], [0, 1])
    counts = run_circuit(circuit, shots=500)
    assert sum(counts.values()) == 500
    assert set(counts.keys()) <= {"00", "01", "10", "11"}


def test_unsupported_gate_raises_a_clear_error():
    circuit = QuantumCircuit(1, 1)
    circuit.u(0.1, 0.2, 0.3, 0)  # not in this adapter's translation vocabulary
    circuit.measure(0, 0)
    with pytest.raises(ValueError, match="not in this adapter's supported translation vocabulary"):
        run_circuit(circuit, shots=10)


def test_submit_job_is_synchronous_and_status_is_immediately_done():
    adapter = get_adapter("cudaq")
    sub = adapter.submit_job("qpp-cpu", [BELL_QASM], shots=256)
    assert sub["status"] == "DONE"
    assert sub["is_real_hardware"] is False
    status = adapter.job_status(sub["job_id"])
    assert status["status"] == "DONE"
    results = adapter.job_results(sub["job_id"])
    assert "counts" in results
    assert sum(results["counts"].values()) == 256


def test_job_results_for_unknown_job_id_reports_a_clear_error():
    adapter = get_adapter("cudaq")
    assert "error" in adapter.job_results("not-a-real-job-id")
