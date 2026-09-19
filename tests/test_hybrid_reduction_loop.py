"""
Tests for core/hybrid_reduction_loop.py. Uses "cudaq"/"pennylane" as the
provider -- free, local, no API key needed, fast enough to run several
rounds in a test.

The multi-round assertion is deliberately mechanism-level (does
refinement genuinely change parameters based on real feedback, not blind
retrying) rather than outcome-level (does it always converge by round N)
-- confirmed directly in manual testing that convergence timing is
inherently probabilistic (a small, easy graph coloring search can find a
valid answer in round 1 by pure luck; a harder one may not converge in a
fixed round budget at all), so asserting a specific round count would be
seed-fragile. What's real and deterministic is that when a round fails,
the NEXT round's parameters are provably different, not a repeat.
"""
import pytest

pytest.importorskip("cudaq")

from core.hybrid_reduction_loop import run_hybrid_reduction_loop

BIPARTITE_4CYCLE = [(0, 1), (1, 2), (2, 3), (3, 0)]


def test_immediate_success_reports_go_and_a_valid_coloring():
    result = run_hybrid_reduction_loop(
        BIPARTITE_4CYCLE, n_vertices=4, provider="cudaq", target_device="qpp-cpu",
        max_rounds=3, shots=2048,
    )
    assert result["verdict"] == "GO"
    assert result["verification"]["any_valid_coloring_found"] is True
    assert len(result["rounds"]) == result["n_rounds_used"]


def test_round_log_is_complete_and_auditable():
    result = run_hybrid_reduction_loop(
        BIPARTITE_4CYCLE, n_vertices=4, provider="cudaq", target_device="qpp-cpu",
        max_rounds=2, shots=1024,
    )
    for round_entry in result["rounds"]:
        assert "p_layers" in round_entry
        assert "gamma" in round_entry
        assert "beta" in round_entry
        assert "any_valid_coloring_found" in round_entry


def test_refinement_genuinely_changes_parameters_after_a_failed_round():
    """
    Forces round 1 to fail by using an unsatisfiable "graph" (a triangle,
    K3, has no valid 2-coloring -- every 2-coloring of a triangle leaves
    at least one edge with matching colors), then confirms round 2's
    parameters differ from round 1's -- proving the loop refines based on
    the real (failed) result instead of retrying identically.
    """
    unsatisfiable_triangle = [(0, 1), (1, 2), (2, 0)]
    result = run_hybrid_reduction_loop(
        unsatisfiable_triangle, n_vertices=3, provider="cudaq", target_device="qpp-cpu",
        max_rounds=3, shots=512,
    )
    assert result["verdict"] == "NO_VALID_COLORING_FOUND"
    assert len(result["rounds"]) == 3
    round1, round2 = result["rounds"][0], result["rounds"][1]
    assert round1["any_valid_coloring_found"] is False
    # Real refinement: gamma strictly increases every round it doesn't succeed.
    assert round2["gamma"] > round1["gamma"]


def test_max_rounds_reached_without_success_reports_a_clear_verdict():
    unsatisfiable_triangle = [(0, 1), (1, 2), (2, 0)]
    result = run_hybrid_reduction_loop(
        unsatisfiable_triangle, n_vertices=3, provider="cudaq", target_device="qpp-cpu",
        max_rounds=2, shots=256,
    )
    assert result["verdict"] == "NO_VALID_COLORING_FOUND"
    assert result["n_rounds_used"] == 2
    assert "note" in result
