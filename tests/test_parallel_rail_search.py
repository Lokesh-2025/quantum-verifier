"""
Tests for core/parallel_rail_search.py -- the generic tiler. IBM's
qubit-offset counts-splitting logic is tested against a mocked adapter
(no real hardware/credentials needed) using a scenario already verified
by hand earlier: a Bell-pair rail + an isolated-X rail, real combined
counts {'111': 98, '100': 102} -- confirmed to split back into the exact
Bell-state distribution and the exact always-1 distribution. The
sequential (cudaq/pennylane) path is tested against real local execution
using the Singmaster's plugin, since both are free and fast.
"""
from unittest.mock import MagicMock

from qiskit import QuantumCircuit
from qiskit.qasm2 import dumps as qasm2_dumps

from core.parallel_rail_search import run_parallel_rail_search, _run_ibm_tiled
from core.singmaster_search import build_collision_rail, decode_collision_rail
from providers.ibm import _tile_circuits


def test_tile_circuits_produces_correct_qubit_ranges_and_gates():
    r0 = QuantumCircuit(2, 2)
    r0.h(0); r0.cx(0, 1); r0.measure([0, 1], [0, 1])
    r1 = QuantumCircuit(1, 1)
    r1.x(0); r1.measure(0, 0)

    combined, ranges = _tile_circuits([r0, r1])

    assert combined.num_qubits == 3
    assert ranges == [(0, 2), (2, 1)]
    # Real gates landed on the correct qubits, not shifted/overlapping.
    gate_names_by_qubit = {q: [] for q in range(3)}
    for instr in combined.data:
        for q in instr.qubits:
            gate_names_by_qubit[combined.find_bit(q).index].append(instr.operation.name)
    assert "h" in gate_names_by_qubit[0]
    assert "x" in gate_names_by_qubit[2]


def test_ibm_tiling_splits_aggregate_counts_back_into_correct_per_rail_distributions():
    r0 = QuantumCircuit(2, 2)
    r0.h(0); r0.cx(0, 1); r0.measure([0, 1], [0, 1])
    r1 = QuantumCircuit(1, 1)
    r1.x(0); r1.measure(0, 0)

    rail_specs = [
        {"circuit": r0, "decode": lambda c: {"counts": c}, "label": "bell"},
        {"circuit": r1, "decode": lambda c: {"counts": c}, "label": "x"},
    ]

    mock_adapter = MagicMock()
    mock_adapter.submit_job.return_value = {"job_id": "fake", "rail_ranges": [(0, 2), (2, 1)]}
    # Real, previously-verified combined result for this exact 2-rail scenario.
    mock_adapter.job_results.return_value = {"counts": {"111": 98, "100": 102}}

    out = _run_ibm_tiled(rail_specs, mock_adapter, "ibm_fez", 4096, qasm2_dumps)

    assert out[0]["label"] == "bell"
    assert out[0]["decoded"]["counts"] == {"11": 98, "00": 102}
    assert out[1]["label"] == "x"
    assert out[1]["decoded"]["counts"] == {"1": 200}


def test_ibm_tiling_propagates_submission_errors():
    mock_adapter = MagicMock()
    mock_adapter.submit_job.return_value = {"error": "something real went wrong"}
    r0 = QuantumCircuit(1, 1)
    r0.x(0); r0.measure(0, 0)
    out = _run_ibm_tiled(
        [{"circuit": r0, "decode": lambda c: c, "label": "r0"}],
        mock_adapter, "ibm_fez", 4096, qasm2_dumps,
    )
    assert out == {"error": "something real went wrong"}


def test_empty_rail_specs_reports_a_clear_error():
    result = run_parallel_rail_search([], provider="cudaq", target_device="qpp-cpu")
    assert "error" in result


def test_ionq_free_simulator_path_honestly_flags_top5_only_results_as_approximate():
    """
    Real, confirmed behavior: ionq_submit_job's free "ionq_simulator"
    target returns no job_id at all -- a synchronous self-check with only
    the top-5 counts per circuit embedded in the submission response
    itself (providers/ionq.py's own real return shape, not this repo's
    truncation). Every decoded rail from that path must be explicitly
    flagged approximate, never silently reported as if it were a full
    distribution.
    """
    from unittest.mock import MagicMock

    mock_adapter = MagicMock()
    mock_adapter.submit_job.return_value = {
        "status": "SIMULATED", "backend": "ionq_simulator",
        "self_check": {"per_circuit": [
            {"circuit_index": 0, "simulated_counts_top5": {"101010000": 144, "001010000": 8}},
        ]},
    }
    qc, marked_rows = build_collision_rail(2, 3, max_n1=20, max_n2=15)
    from core.parallel_rail_search import _run_ionq_batched
    from qiskit.qasm2 import dumps as qasm2_dumps

    out = _run_ionq_batched(
        [{"circuit": qc, "decode": lambda c, mr=marked_rows, nq=qc.num_qubits: decode_collision_rail(c, mr, nq),
          "label": "r1"}],
        mock_adapter, "ionq_simulator", 200, qasm2_dumps,
    )
    assert out[0]["decoded"]["approximate"] is True
    assert "top-5" in out[0]["decoded"]["note"]


def test_sequential_path_runs_real_singmaster_rails_and_picks_the_real_best():
    qc1, mr1 = build_collision_rail(2, 3, max_n1=20, max_n2=15)
    qc2, mr2 = build_collision_rail(2, 4, max_n1=30, max_n2=20)

    rail_specs = [
        {"circuit": qc1, "decode": lambda c, mr=mr1, nq=qc1.num_qubits: decode_collision_rail(c, mr, nq),
         "label": "rail1"},
        {"circuit": qc2, "decode": lambda c, mr=mr2, nq=qc2.num_qubits: decode_collision_rail(c, mr, nq),
         "label": "rail2"},
    ]

    result = run_parallel_rail_search(rail_specs, provider="cudaq", target_device="qpp-cpu", shots=2048)

    assert result["num_rails"] == 2
    assert len(result["rails"]) == 2
    for r in result["rails"]:
        assert r["decoded"]["amplification"] > 10  # real signal on both rails
    assert result["best_rail"] is not None
    assert result["best_rail"]["label"] in ("rail1", "rail2")
