"""
Classical-reduction pre-check -- informational, non-BLOCKing (matching
the same "earn integration before it can BLOCK" pattern
ground_truth_significance_test and register_mapping_check both followed
when first added).

Deliberately does NOT reinvent two things this repo already does:
  - Clifford circuits: core/stabilizer.py already answers this exactly,
    in polynomial time, unbounded qubit count (Gottesman-Knill) -- cudaq
    adds nothing there and is not invoked for that case.
  - "Strip entangling gates and diff the result": already
    core/control_experiment.py's build_control_circuit + falsify() --
    not redone here with a different simulator underneath.

What's actually new: (1) an independent second exact-statevector
implementation of ideal_simulation's answer, same "don't trust one
method" spirit as cross_check_fidelity_estimate/diff_compilers; (2) at
circuit sizes beyond a laptop's Aer statevector RAM ceiling, an honest
attempt at the same exact computation on cudaq's target (qpp-cpu here;
nvidia automatically on real GPU hardware -- this repo's dev machine has
no NVIDIA GPU, confirmed via cudaq.set_target("nvidia") raising
RuntimeError: "Invalid target name (nvidia)" on this platform's wheel, so
success at a scale beyond Aer's own ceiling is NOT verified here, only
clean failure-reporting is); (3) a circuit-intrinsic entanglement-entropy
measurement of the real, unmodified circuit's exact final state.
"""
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector, partial_trace, entropy

from core.stabilizer import is_clifford_circuit, verify_stabilizer_circuit

DISAGREEMENT_TVD_THRESHOLD = 0.01  # exact-vs-exact, so a real disagreement is tiny
LOW_ENTANGLEMENT_ENTROPY_THRESHOLD = 0.05


def _exact_probabilities(sv: Statevector) -> dict:
    return sv.probabilities_dict()


def _total_variation_distance(p: dict, q: dict) -> float:
    keys = set(p) | set(q)
    return 0.5 * sum(abs(p.get(k, 0.0) - q.get(k, 0.0)) for k in keys)


def classical_reduction_check(circuit: QuantumCircuit) -> dict:
    clifford = is_clifford_circuit(circuit)
    if clifford["is_clifford"]:
        return {
            "applicable": True, "method": "stabilizer_exact_deferral",
            "classically_resolvable": True,
            "stabilizer_result": verify_stabilizer_circuit(circuit),
            "note": "Clifford circuit -- exactly solved in polynomial time by "
                    "core/stabilizer.py's Gottesman-Knill tableau; a general-purpose "
                    "classical simulator was not invoked because it adds nothing here.",
        }

    try:
        import cudaq  # noqa: F401 -- import-availability probe only
    except ImportError:
        return {"applicable": False,
                "note": "cudaq is not installed -- classical-reduction pre-check skipped."}

    from adapters.cudaq_adapter import get_exact_statevector, select_target

    try:
        cudaq_sv = get_exact_statevector(circuit)
    except Exception as e:
        return {
            "applicable": True, "method": "cudaq_exact_statevector",
            "target_used": select_target(), "classically_resolvable": None,
            "error": f"cudaq exact simulation failed/exceeded resources on this target: {e}. "
                     "On a machine with a real NVIDIA GPU (the 'nvidia' target) this may "
                     "succeed at a larger qubit count -- not verified in this environment.",
        }

    unmeasured = circuit.remove_final_measurements(inplace=False)
    aer_sv = Statevector.from_instruction(unmeasured)

    tvd = _total_variation_distance(_exact_probabilities(cudaq_sv), _exact_probabilities(aer_sv))
    n = circuit.num_qubits
    half = n // 2
    ent = float(entropy(partial_trace(cudaq_sv, list(range(half, n))))) if half else 0.0
    low_entanglement = ent < LOW_ENTANGLEMENT_ENTROPY_THRESHOLD

    return {
        "applicable": True, "method": "cudaq_exact_statevector",
        "target_used": select_target(),
        "classically_resolvable": True,
        "cross_check_vs_aer_exact_statevector": {
            "total_variation_distance": round(tvd, 8),
            "significant_disagreement": bool(tvd > DISAGREEMENT_TVD_THRESHOLD),
        },
        "entanglement_entropy_half_bipartition": round(ent, 4),
        "low_entanglement": bool(low_entanglement),
        "note": (
            f"Exact classical simulation succeeded on cudaq's '{select_target()}' target "
            f"({n} qubits) -- independent cross-check against qiskit's exact statevector "
            + ("agrees." if tvd <= DISAGREEMENT_TVD_THRESHOLD else "DISAGREES -- investigate.")
            + (f" Entanglement entropy is low ({ent:.3f}) despite non-Clifford gates -- "
               "this circuit's final state is close to a product state; worth checking "
               "whether the entangling gates present are doing anything for this circuit's claim."
               if low_entanglement else "")
        ),
    }
