"""
Tests for the stabilizer/Clifford checkable-structure verifier
(core/stabilizer.py) — generalizes the GHZ trick in core/templates.py to
any Clifford-only circuit.

Everything here runs locally, no API key, no hardware, no cost — the
stabilizer tableau computation is pure classical math.
"""
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector

from core.stabilizer import (
    is_clifford_circuit,
    verify_stabilizer_circuit,
    verify_stabilizer_hardware_result,
)


def test_ghz_matches_known_exact_distribution():
    qc = QuantumCircuit(3, 3)
    qc.h(0); qc.cx(0, 1); qc.cx(0, 2)
    qc.measure(range(3), range(3))
    result = verify_stabilizer_circuit(qc)
    assert result["applicable"]
    assert result["exact_probabilities"] == {"000": 0.5, "111": 0.5}
    assert result["support_size"] == 2


def test_complex_clifford_circuit_matches_statevector_simulation():
    """Cross-check against real state-vector simulation on a non-trivial
    circuit, not just the easy GHZ case."""
    qc = QuantumCircuit(4, 4)
    qc.h(0); qc.h(2)
    qc.s(1)
    qc.cx(0, 1)
    qc.cz(2, 3)
    qc.x(3)
    qc.cx(1, 2)
    qc.measure(range(4), range(4))

    result = verify_stabilizer_circuit(qc)
    unitary_only = qc.remove_final_measurements(inplace=False)
    sv_probs = {k: round(v, 6) for k, v in Statevector(unitary_only).probabilities_dict().items() if v > 1e-9}

    assert result["applicable"]
    assert result["exact_probabilities"] == sv_probs


def test_scales_to_150_qubits_where_statevector_simulation_is_impossible():
    n = 150
    qc = QuantumCircuit(n, n)
    qc.h(0)
    for i in range(1, n):
        qc.cx(0, i)
    qc.measure(range(n), range(n))

    result = verify_stabilizer_circuit(qc)
    assert result["applicable"]
    assert result["n_qubits"] == 150
    assert result["support_size"] == 2
    assert set(result["exact_probabilities"].keys()) == {"0" * 150, "1" * 150}


def test_non_clifford_gate_is_honestly_reported_not_guessed():
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.rz(0.37, 1)  # arbitrary angle -- not Clifford
    qc.cx(0, 1)
    qc.measure(range(2), range(2))

    check = is_clifford_circuit(qc)
    assert check["is_clifford"] is False

    result = verify_stabilizer_circuit(qc)
    assert result["applicable"] is False
    assert "non-Clifford" in result["reason"]


def test_hardware_result_verification_computes_real_fidelity_lower_bound():
    qc = QuantumCircuit(3, 3)
    qc.h(0); qc.cx(0, 1); qc.cx(0, 2)
    qc.measure(range(3), range(3))

    noisy_counts = {"000": 480, "111": 470, "010": 30, "101": 20}
    result = verify_stabilizer_hardware_result(qc, noisy_counts)
    assert result["applicable"]
    assert result["fidelity_lower_bound"] == 0.95
    assert result["valid_shots"] == 950
    assert len(result["top_invalid_bitstrings"]) == 2


def test_hardware_result_verification_handles_empty_counts():
    qc = QuantumCircuit(2, 2)
    qc.h(0); qc.cx(0, 1)
    qc.measure(range(2), range(2))
    result = verify_stabilizer_hardware_result(qc, {})
    assert result["applicable"] is False
