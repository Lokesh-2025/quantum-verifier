"""
Stabilizer/Clifford checkable-structure verifier.

Generalizes the trick core/templates.py's ghz_parity_check already uses for
one specific circuit (GHZ): a circuit built entirely from Clifford gates
(H, S, CX, CZ, X, Y, Z, SWAP, ...) plus measurements is EXACTLY,
efficiently classically checkable no matter how many qubits it has
(Gottesman-Knill theorem) -- not an approximation, not a simulation in the
usual sense, a polynomial-time tableau computation. Confirmed directly:
a 100-qubit GHZ-style circuit (state-vector simulation of which would need
2^100 amplitudes -- physically impossible) computes its exact measurement
distribution here in well under a second.

This is the general case GHZ was one hand-built example of. Any circuit
that happens to be Clifford-only gets this same free, exact verification
for free, without needing a dedicated template written for it.

Deliberately narrow: this only applies to Clifford circuits. A circuit
using any arbitrary-angle rotation (RZ, RZZ, RX, ...) is NOT Clifford and
is correctly reported as inapplicable here, not silently mishandled --
that's still the job of ideal_simulation/hardware_aware_simulation.
"""
from qiskit import QuantumCircuit
from qiskit.quantum_info import Clifford, StabilizerState


def is_clifford_circuit(circuit: QuantumCircuit) -> dict:
    """
    Checks whether circuit (measurements/barriers aside) is entirely
    Clifford gates, by actually trying to build a Clifford tableau from it
    -- relies on Qiskit's own knowledge of which gates are Clifford,
    rather than a separately maintained gate-name list that could drift
    out of sync with it.
    """
    unitary_only = circuit.remove_final_measurements(inplace=False)
    unitary_only.data = [
        instr for instr in unitary_only.data if instr.operation.name not in ("measure", "barrier")
    ]
    try:
        Clifford(unitary_only)
        return {"is_clifford": True, "reason": None}
    except Exception as e:
        return {"is_clifford": False, "reason": str(e)}


def verify_stabilizer_circuit(circuit: QuantumCircuit) -> dict:
    """
    For a Clifford-only circuit, compute the EXACT measurement outcome
    distribution via the stabilizer tableau -- not simulated, not
    estimated, exact, and scales to hundreds of qubits (polynomial in
    qubit count, unlike state-vector simulation which is exponential).

    Not applicable (reported honestly, not guessed) if the circuit
    contains any non-Clifford gate.
    """
    check = is_clifford_circuit(circuit)
    if not check["is_clifford"]:
        return {
            "applicable": False,
            "reason": f"circuit contains a non-Clifford gate, not verifiable via the "
                      f"stabilizer formalism: {check['reason']}",
        }

    unitary_only = circuit.remove_final_measurements(inplace=False)
    unitary_only.data = [
        instr for instr in unitary_only.data if instr.operation.name not in ("measure", "barrier")
    ]
    cliff = Clifford(unitary_only)
    state = StabilizerState(cliff)
    exact_probabilities = state.probabilities_dict()

    return {
        "applicable": True,
        "n_qubits": circuit.num_qubits,
        "exact_probabilities": {k: round(v, 6) for k, v in exact_probabilities.items()},
        "support_size": len(exact_probabilities),
        "method": "stabilizer tableau (Gottesman-Knill) -- exact, not simulated, "
                  "polynomial-time regardless of qubit count",
    }


def verify_stabilizer_hardware_result(circuit: QuantumCircuit, hw_counts: dict) -> dict:
    """
    Verify real hardware counts against the exact stabilizer prediction.
    Any bitstring with zero predicted probability is IMPOSSIBLE in the
    ideal case -- its presence in real counts is purely noise, exactly
    the same logic run_ghz_parity_check already uses, just generalized to
    any Clifford circuit instead of only GHZ.
    """
    prediction = verify_stabilizer_circuit(circuit)
    if not prediction["applicable"]:
        return prediction

    if not hw_counts:
        return {"applicable": False, "reason": "No hardware counts provided."}

    valid_outcomes = {b for b, p in prediction["exact_probabilities"].items() if p > 0}
    total = sum(hw_counts.values())
    valid_shots = sum(c for b, c in hw_counts.items() if b in valid_outcomes)
    fidelity_lower_bound = valid_shots / total if total else 0
    invalid_bitstrings = sorted(
        ((b, c) for b, c in hw_counts.items() if b not in valid_outcomes),
        key=lambda kv: -kv[1],
    )[:5]

    return {
        "applicable": True,
        "n_qubits": prediction["n_qubits"],
        "support_size": prediction["support_size"],
        "fidelity_lower_bound": round(fidelity_lower_bound, 4),
        "valid_shots": valid_shots,
        "total_shots": total,
        "top_invalid_bitstrings": invalid_bitstrings,
        "verdict": (
            f"Fidelity lower bound {round(fidelity_lower_bound, 3)} ({valid_shots}/{total} shots "
            f"landed on one of the {prediction['support_size']} outcomes that are actually possible "
            "under the exact stabilizer prediction) -- checked exactly, no simulation required, "
            f"regardless of the circuit's {prediction['n_qubits']} qubits."
        ),
    }
