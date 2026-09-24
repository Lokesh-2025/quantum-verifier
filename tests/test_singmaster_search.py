"""
Tests for core/singmaster_search.py -- the first real plugin for the
generic parallel-rail tiler.
"""
from core.singmaster_search import find_collision, build_collision_rail, decode_collision_rail
from adapters.cudaq_adapter import run_circuit


def test_find_collision_matches_the_real_known_16_2_10_3_collision():
    """
    C(16,2) = C(10,3) = 120 -- already validated elsewhere in this
    project's real history (README.md, providers/ionq.py test coverage).
    """
    c = find_collision(2, 3, max_n1=20, max_n2=15)
    assert c == {"n1": 16, "n2": 10, "value": 120}


def test_find_collision_returns_none_when_no_collision_in_range():
    c = find_collision(40, 41, max_n1=5, max_n2=5)
    assert c is None


def test_build_collision_rail_returns_none_when_no_collision():
    qc, marked_rows = build_collision_rail(40, 41, max_n1=5, max_n2=5)
    assert qc is None
    assert marked_rows is None


def test_build_collision_rail_produces_a_real_circuit_for_a_known_collision():
    qc, marked_rows = build_collision_rail(2, 3, max_n1=20, max_n2=15)
    assert qc is not None
    assert qc.num_qubits == 9  # bits1=ceil(log2(21))=5, bits2=ceil(log2(16))=4
    assert len(marked_rows) == 1


def test_decoded_amplification_is_real_and_strongly_favors_the_marked_state():
    """
    Runs the real circuit locally (CUDA-Q, free) and confirms the decoded
    amplification is genuinely high -- the marked state should dominate
    the distribution, not just be present.
    """
    qc, marked_rows = build_collision_rail(2, 3, max_n1=20, max_n2=15)
    counts = run_circuit(qc, shots=4096)
    decoded = decode_collision_rail(counts, marked_rows, qc.num_qubits)
    assert decoded["amplification"] > 50  # real signal, not noise-level
    assert decoded["marked_fraction"] > 0.3  # marked state genuinely dominant


def test_decode_collision_rail_handles_empty_counts():
    result = decode_collision_rail({}, [5], 4)
    assert "error" in result
