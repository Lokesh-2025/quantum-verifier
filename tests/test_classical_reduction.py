"""
Tests for core/classical_reduction.py -- the non-reinvention guarantees
(Clifford circuits defer to core/stabilizer.py, cudaq never invoked for
that path) are the load-bearing assertions here, not just the happy path.
"""
import pytest
from unittest.mock import patch

pytest.importorskip("cudaq")

from qiskit import QuantumCircuit

from core.classical_reduction import classical_reduction_check


def _ghz(n=3) -> QuantumCircuit:
    qc = QuantumCircuit(n, n)
    qc.h(0)
    for i in range(n - 1):
        qc.cx(i, i + 1)
    qc.measure(range(n), range(n))
    return qc


def test_clifford_circuit_defers_to_stabilizer():
    result = classical_reduction_check(_ghz())
    assert result["applicable"] is True
    assert result["method"] == "stabilizer_exact_deferral"
    assert result["classically_resolvable"] is True
    assert "stabilizer_result" in result


def test_cudaq_is_never_invoked_for_a_clifford_circuit():
    """
    The real non-reinvention guarantee: not just documented, actually
    enforced. Monkeypatches cudaq.set_target to raise if called at all --
    if the Clifford path ever accidentally starts invoking cudaq, this
    test fails loudly.
    """
    import cudaq
    with patch.object(cudaq, "set_target",
                       side_effect=AssertionError("cudaq must not be invoked for Clifford circuits")):
        result = classical_reduction_check(_ghz())
        assert result["method"] == "stabilizer_exact_deferral"


def test_non_clifford_entangled_circuit_cross_checks_against_aer_exactly():
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.rz(0.3, 0)  # non-Clifford angle
    qc.cx(0, 1)
    qc.measure([0, 1], [0, 1])

    result = classical_reduction_check(qc)
    assert result["applicable"] is True
    assert result["method"] == "cudaq_exact_statevector"
    # exact-vs-exact, so a real disagreement would be far larger than this
    assert result["cross_check_vs_aer_exact_statevector"]["total_variation_distance"] < 1e-6
    assert result["cross_check_vs_aer_exact_statevector"]["significant_disagreement"] is False


def test_entangled_circuit_reports_high_entanglement_entropy():
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.rz(0.3, 0)
    qc.cx(0, 1)
    qc.measure([0, 1], [0, 1])
    result = classical_reduction_check(qc)
    assert result["entanglement_entropy_half_bipartition"] > 0.5
    assert result["low_entanglement"] is False


def test_product_state_circuit_reports_low_entanglement_entropy():
    """
    Two independent single-qubit rotations, no entangling gate at all --
    a non-Clifford circuit (so it doesn't defer to the stabilizer path)
    whose real entanglement is exactly zero. This is the one check
    genuinely different in kind from falsify_claim/the Clifford check:
    no hypothesis about which gate to strip, not binary/structural, a
    continuous measurement of the real circuit as-is.
    """
    qc = QuantumCircuit(2, 2)
    qc.rx(0.4, 0)
    qc.ry(0.2, 1)
    qc.measure([0, 1], [0, 1])
    result = classical_reduction_check(qc)
    assert result["entanglement_entropy_half_bipartition"] == pytest.approx(0.0, abs=1e-6)
    assert result["low_entanglement"] is True


def test_result_is_json_serializable():
    """
    Regression test: numpy bool/float types from the TVD/entropy
    comparisons must be cast to plain Python types, or this breaks
    verify()'s eventual json.dumps(result) call in mcp_server.py.
    """
    import json
    qc = QuantumCircuit(2, 2)
    qc.rx(0.4, 0)
    qc.cx(0, 1)
    qc.measure([0, 1], [0, 1])
    result = classical_reduction_check(qc)
    json.dumps(result)  # raises TypeError if anything isn't JSON-serializable
