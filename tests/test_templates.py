"""
Tests for checkable-structure experiment templates (core/templates.py).

Everything here runs on local Aer — no API key, no hardware, no cost.
Confirms each generator produces a circuit whose ideal (noiseless) output
is correctly classified by its paired classical verifier, and that the
verifier correctly rejects invalid candidates it's handed directly.
"""
from core.verifier import ideal_simulation
from core.templates import (
    ghz_parity_check_circuit,
    verify_ghz_parity,
    graph_coloring_oracle_circuit,
    verify_graph_coloring,
)


# --------------------------------------------------------------- GHZ parity


def test_ghz_circuit_ideal_output_is_only_all_zero_or_all_one():
    qc = ghz_parity_check_circuit(5)
    sim = ideal_simulation(qc, shots=2048)
    result = verify_ghz_parity(sim["counts"])
    assert result["applicable"]
    assert result["fidelity_lower_bound"] == 1.0
    assert result["top_invalid_bitstrings"] == []


def test_ghz_verify_flags_invalid_bitstrings_from_noisy_counts():
    noisy_counts = {"000": 900, "111": 900, "010": 100, "101": 100}
    result = verify_ghz_parity(noisy_counts)
    assert result["fidelity_lower_bound"] == 0.9
    assert result["valid_shots"] == 1800
    assert len(result["top_invalid_bitstrings"]) == 2


def test_ghz_circuit_rejects_fewer_than_two_qubits():
    try:
        ghz_parity_check_circuit(1)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_ghz_verify_handles_empty_counts():
    result = verify_ghz_parity({})
    assert result["applicable"] is False


# --------------------------------------------------------- graph coloring


def test_graph_coloring_oracle_finds_valid_coloring_on_bipartite_graph():
    # A 4-cycle (0-1-2-3-0) is bipartite: {0,2} vs {1,3} is a valid 2-coloring.
    edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
    qc = graph_coloring_oracle_circuit(edges, n_vertices=4, p_layers=4)
    sim = ideal_simulation(qc, shots=4096)
    result = verify_graph_coloring(sim["counts"], edges, top_n=16)
    assert result["applicable"]
    assert result["any_valid_coloring_found"], (
        "oracle should amplify at least one true valid 2-coloring into the "
        "top 16 candidates for a simple bipartite 4-cycle"
    )


def test_verify_graph_coloring_correctly_classifies_known_candidates():
    edges = [(0, 1), (1, 2)]
    # "010": vertex0=0,vertex1=1,vertex2=0 -> edges (0,1) differ, (1,2) differ -> valid
    # "000": all same -> invalid
    counts = {"010": 800, "000": 200}
    result = verify_graph_coloring(counts, edges, top_n=5)
    by_bits = {c["bitstring"]: c["valid_coloring"] for c in result["top_candidates"]}
    assert by_bits["010"] is True
    assert by_bits["000"] is False
    assert result["any_valid_coloring_found"] is True


def test_graph_coloring_oracle_rejects_edge_outside_vertex_range():
    try:
        graph_coloring_oracle_circuit([(0, 5)], n_vertices=3)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_verify_graph_coloring_handles_empty_counts():
    result = verify_graph_coloring({}, edges=[(0, 1)])
    assert result["applicable"] is False
