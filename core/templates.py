"""
Checkable-structure experiment templates.

The only honest way to verify a result once a circuit is too big to
classically simulate (roughly 50+ qubits) is to only run problems where the
CORRECT ANSWER is cheap to check classically, even though finding it was
hard — the same principle equality_oracle_search (quantum-hardware-mcp)
already uses for Pascal's-triangle collisions: Lucas' theorem makes checking
a candidate O(1), even though the search space is exponential.

Each template here follows that same shape:
  1. A generator that builds a circuit whose claimed answer is classically
     cheap to verify.
  2. A verifier that checks candidates from real hardware output against
     that cheap classical predicate — never against a full-state simulation.

Two templates so far:
  - ghz_parity_check: verifiable at ANY qubit count, because the ideal
    target (a GHZ state) is a stabilizer state with an analytically known
    signature — no simulation needed regardless of n_qubits.
  - graph_coloring_oracle: an LNAA-style oracle (same RZZ-phase-kick +
    RX-mixing structure as equality_oracle_search) that amplifies valid
    2-colorings of a graph; validity of any candidate is an O(edges)
    classical check.

A third category ("symmetry-protected invariants") was scoped out for now —
it needs a concrete conserved-quantity circuit to be worth building, not a
generic framework guess.
"""
from qiskit import QuantumCircuit

from core.verifier import hardware_aware_simulation


# --------------------------------------------------------------- GHZ parity


def ghz_parity_check_circuit(n_qubits: int) -> QuantumCircuit:
    """
    Build an n-qubit GHZ state: H on qubit 0, then a CX chain entangling
    every other qubit to it. Ideal measurement outcomes are ONLY "0"*n or
    "1"*n — any other bitstring is impossible in the ideal case, which
    makes this checkable at any qubit count without simulating the state.
    """
    if n_qubits < 2:
        raise ValueError("GHZ parity check needs at least 2 qubits")
    qc = QuantumCircuit(n_qubits, n_qubits)
    qc.h(0)
    for i in range(1, n_qubits):
        qc.cx(0, i)
    qc.measure(range(n_qubits), range(n_qubits))
    return qc


def verify_ghz_parity(counts: dict) -> dict:
    """
    Classically verify hardware output against the GHZ structure. Valid
    outcomes are the all-0 and all-1 bitstrings; anything else is only
    possible due to noise. This gives a fidelity LOWER BOUND
    (P(all-0) + P(all-1)) that's exact to check at any n_qubits.
    """
    if not counts:
        return {"applicable": False, "note": "No counts available."}
    total = sum(counts.values())
    n_qubits = len(next(iter(counts.keys())))
    all_zero = "0" * n_qubits
    all_one = "1" * n_qubits
    valid_shots = counts.get(all_zero, 0) + counts.get(all_one, 0)
    fidelity_lower_bound = valid_shots / total if total else 0
    invalid_bitstrings = sorted(
        ((b, c) for b, c in counts.items() if b not in (all_zero, all_one)),
        key=lambda kv: -kv[1],
    )[:5]
    return {
        "applicable": True,
        "n_qubits": n_qubits,
        "fidelity_lower_bound": round(fidelity_lower_bound, 4),
        "valid_shots": valid_shots,
        "total_shots": total,
        "top_invalid_bitstrings": invalid_bitstrings,
        "verdict": (
            f"GHZ fidelity lower bound is {round(fidelity_lower_bound, 3)} "
            f"({valid_shots}/{total} shots landed on the only two valid "
            "outcomes) — checked exactly, no simulation required regardless "
            "of qubit count."
        ),
    }


# --------------------------------------------------------- graph coloring


def graph_coloring_oracle_circuit(
    edges: list,
    n_vertices: int,
    p_layers: int = 3,
    gamma: float = 1.0,
    beta: float = 0.8,
) -> QuantumCircuit:
    """
    LNAA-style oracle that amplifies valid 2-colorings of a graph: one
    qubit per vertex, RZZ phase-kick per edge (rewards qubits ending up in
    DIFFERENT states, i.e. adjacent vertices getting different colors),
    RX mixing between layers — same layered structure equality_oracle_search
    uses for the Pascal's-triangle equality oracle, just with the opposite
    target (favor disagreement across an edge instead of agreement across
    a register pair).

    A valid 2-coloring exists only if the graph is bipartite; this circuit
    searches for one, but does not itself prove existence — that comes from
    classically checking the amplified candidates.
    """
    if n_vertices < 2:
        raise ValueError("Graph coloring oracle needs at least 2 vertices")
    for i, j in edges:
        if not (0 <= i < n_vertices and 0 <= j < n_vertices):
            raise ValueError(f"Edge {(i, j)} references a vertex outside 0..{n_vertices - 1}")

    qc = QuantumCircuit(n_vertices, n_vertices)
    qc.h(range(n_vertices))
    for _ in range(p_layers):
        for i, j in edges:
            qc.rzz(-gamma, i, j)  # negative sign rewards differing bits, not matching ones
        qc.rx(beta, range(n_vertices))
    qc.measure(range(n_vertices), range(n_vertices))
    return qc


def verify_graph_coloring(counts: dict, edges: list, top_n: int = 10) -> dict:
    """
    Classically verify candidate colorings from hardware output. Checking
    one candidate is O(len(edges)) — cheap even when the search space
    (2^n_vertices colorings) is exponential.
    """
    if not counts:
        return {"applicable": False, "note": "No counts available."}
    total = sum(counts.values())

    def is_valid(bitstring: str) -> bool:
        return all(bitstring[i] != bitstring[j] for i, j in edges)

    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:top_n]
    checked = [
        {"bitstring": b, "shots": c, "fraction": round(c / total, 4), "valid_coloring": is_valid(b)}
        for b, c in ranked
    ]
    valid_shots = sum(c for b, c in counts.items() if is_valid(b))
    return {
        "applicable": True,
        "top_candidates": checked,
        "valid_shots": valid_shots,
        "total_shots": total,
        "valid_fraction": round(valid_shots / total, 4) if total else 0,
        "any_valid_coloring_found": any(c["valid_coloring"] for c in checked),
        "verdict": (
            "Found a verified-valid 2-coloring among the amplified candidates."
            if any(c["valid_coloring"] for c in checked) else
            "No valid 2-coloring among the top candidates — either the graph "
            "isn't bipartite, or the oracle needs more layers/shots."
        ),
    }


# ------------------------------------------------------------- orchestrators


def run_ghz_parity_check(n_qubits: int, provider: str, target_device: str, shots: int = 4096) -> dict:
    """
    One-call version: build the GHZ circuit, run it through hardware-aware
    simulation, and classically verify the result — same one-call shape as
    verify_experiment/falsify_claim.

    IBM's hardware-aware path returns a fidelity estimate, not raw counts,
    so this only produces a checkable verdict on the IonQ path today (same
    limitation falsify_claim already has and documents).
    """
    circuit = ghz_parity_check_circuit(n_qubits)
    sim = hardware_aware_simulation(circuit, provider, target_device, shots)
    if "error" in sim:
        return {"error": sim["error"]}
    result = {
        "n_qubits": n_qubits, "provider": provider, "target_device": target_device,
        "simulation_type": sim.get("simulation_type"),
    }
    result.update(verify_ghz_parity(sim.get("counts")))
    return result


def run_graph_coloring_search(
    edges: list,
    n_vertices: int,
    provider: str,
    target_device: str,
    p_layers: int = 3,
    gamma: float = 1.0,
    beta: float = 0.8,
    shots: int = 4096,
    top_n: int = 10,
) -> dict:
    """
    One-call version: build the graph-coloring oracle, run it through
    hardware-aware simulation, and classically verify the top candidates.

    Same IBM/counts limitation as run_ghz_parity_check above.
    """
    circuit = graph_coloring_oracle_circuit(edges, n_vertices, p_layers, gamma, beta)
    sim = hardware_aware_simulation(circuit, provider, target_device, shots)
    if "error" in sim:
        return {"error": sim["error"]}
    result = {
        "n_vertices": n_vertices, "edges": edges, "provider": provider, "target_device": target_device,
        "simulation_type": sim.get("simulation_type"),
    }
    result.update(verify_graph_coloring(sim.get("counts"), edges, top_n))
    return result
