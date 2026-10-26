"""
verify_experiment on a Clifford circuit with many qubits in superposition used
to return every outcome (2**n entries). Past 16 MB, Claude Code closes the MCP
connection, so the report caps the list. The full list must stay intact inside
core/stabilizer.py, which verify_stabilizer_hardware_result relies on.
"""
import json

from qiskit import QuantumCircuit

from core.classical_reduction import MAX_OUTCOMES_IN_REPORT, classical_reduction_check
from core.stabilizer import verify_stabilizer_circuit


def _superposed(n: int) -> QuantumCircuit:
    qc = QuantumCircuit(n, n)
    qc.h(range(n))
    qc.measure(range(n), range(n))
    return qc


def test_large_outcome_list_is_capped_but_total_is_kept():
    result = classical_reduction_check(_superposed(12))["stabilizer_result"]
    assert len(result["exact_probabilities"]) == MAX_OUTCOMES_IN_REPORT
    assert result["exact_probabilities_truncated"] is True
    assert result["support_size"] == 2 ** 12
    assert len(json.dumps(result)) < 20_000


def test_small_outcome_list_is_untouched():
    result = classical_reduction_check(_superposed(3))["stabilizer_result"]
    assert len(result["exact_probabilities"]) == 8
    assert "exact_probabilities_truncated" not in result


def test_stabilizer_module_still_returns_the_full_list():
    full = verify_stabilizer_circuit(_superposed(12))
    assert len(full["exact_probabilities"]) == 2 ** 12
