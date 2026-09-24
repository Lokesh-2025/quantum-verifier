"""
Singmaster's Conjecture collision search -- the first real plugin for the
generic parallel-rail tiler (core/parallel_rail_search.py). Asks: does
C(n1, k1) = C(n2, k2) for some real n1, n2? (a real collision in Pascal's
Triangle).

The classical collision-finding math and the LNAA circuit-building math
here are both reused verbatim from quantum-hardware-mcp/server.py's
proven, already-hardware-confirmed encode_collision_problem and
run_search_experiment -- same defaults (gamma=2.589, beta=0.501), same
Ising h_i derivation, same bit-encoding convention. Restructured into the
build/decode-function shape this project's own graph_coloring_oracle_circuit/
verify_graph_coloring already establish (core/templates.py) -- a builder
returns a real QuantumCircuit, a decoder takes counts and returns a
verdict dict -- rather than quantum-hardware-mcp's JSON-string-returning
shape, since that's what plugs directly into the tiler.
"""
from math import comb, ceil, log2

from qiskit import QuantumCircuit


def find_collision(k1: int, k2: int, max_n1: int = 50, max_n2: int = 30) -> dict | None:
    """
    Real classical search: does C(n1,k1) = C(n2,k2) for some n1 in
    [k1, max_n1], n2 in [k2, max_n2]? Returns the largest non-trivial
    (value > 1) collision found, or None if no collision exists in range
    -- same "largest-value collision drives the encoding" choice as
    quantum-hardware-mcp's encode_collision_problem.
    """
    table1 = {}
    for n in range(k1, max_n1 + 1):
        table1.setdefault(comb(n, k1), []).append(n)
    table2 = {}
    for n in range(k2, max_n2 + 1):
        table2.setdefault(comb(n, k2), []).append(n)

    collisions = []
    for v in sorted(set(table1) & set(table2)):
        for n1 in table1[v]:
            for n2 in table2[v]:
                collisions.append({"n1": n1, "n2": n2, "value": v})

    non_trivial = [c for c in collisions if c["value"] > 1]
    if non_trivial:
        return max(non_trivial, key=lambda c: c["value"])
    return collisions[0] if collisions else None


def build_collision_rail(k1: int, k2: int, max_n1: int = 50, max_n2: int = 30,
                          p_layers: int = 2, gamma: float = 2.589, beta: float = 0.501):
    """
    Builds a real LNAA QuantumCircuit that amplifies the bit pattern of a
    real collision C(n1,k1) = C(n2,k2), or returns None if no collision
    exists for this (k1,k2) pair in range -- mirrors run_parallel_
    collision_search's real behavior of dropping rails with no collision
    rather than building a meaningless circuit.

    Returns (circuit, marked_rows) -- marked_rows (a list of Qiskit-
    integer bitstrings, same MSB-first convention as everywhere else in
    this project) is needed by decode_collision_rail to score the result.
    """
    collision = find_collision(k1, k2, max_n1, max_n2)
    if collision is None:
        return None, None

    bits1 = max(1, ceil(log2(max_n1 + 1)))
    bits2 = max(1, ceil(log2(max_n2 + 1)))
    num_qubits = bits1 + bits2

    n1, n2 = collision["n1"], collision["n2"]
    conditions = {}
    for i in range(bits1):
        conditions[i] = (n1 >> i) & 1
    for i in range(bits2):
        conditions[bits1 + i] = (n2 >> i) & 1

    qbits = [(n1 >> i) & 1 for i in range(bits1)] + [(n2 >> i) & 1 for i in range(bits2)]
    marked_row = int("".join(str(b) for b in reversed(qbits)), 2)

    h_coeffs = {q: (1.0 if v == 1 else -1.0) for q, v in conditions.items()}

    qc = QuantumCircuit(num_qubits, num_qubits)
    qc.h(range(num_qubits))
    for _ in range(p_layers):
        for q_idx, h in h_coeffs.items():
            qc.rz(2 * h * gamma, q_idx)
        for i in range(num_qubits):
            qc.rx(2 * beta, i)
    qc.measure(range(num_qubits), range(num_qubits))

    return qc, [marked_row]


def decode_collision_rail(counts: dict, marked_rows: list, num_qubits: int) -> dict:
    """
    Real amplification-vs-random-baseline math, extracted from
    quantum-hardware-mcp's run_parallel_collision_search into its own
    reusable function instead of staying inlined in a tiling loop.
    """
    if "error" in counts:
        return {"error": counts["error"]}
    if not counts:
        return {"error": "No counts to decode."}

    total_shots = sum(counts.values())
    marked_strs = {format(r, f"0{num_qubits}b") for r in marked_rows}
    marked_shots = sum(c for bits, c in counts.items() if bits in marked_strs)

    marked_fraction = marked_shots / total_shots if total_shots else 0.0
    random_baseline = len(marked_rows) / (2 ** num_qubits)
    amplification = (marked_fraction / random_baseline) if random_baseline else 0.0

    top5 = sorted(counts.items(), key=lambda kv: -kv[1])[:5]

    return {
        "total_shots": total_shots,
        "marked_shots": marked_shots,
        "marked_fraction": round(marked_fraction, 4),
        "random_baseline": round(random_baseline, 6),
        "amplification": round(amplification, 4),
        "top_5_states": top5,
    }
