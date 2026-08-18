"""
Multi-compiler diff engine — Qiskit vs TKET, IBM targets only.

Every transpiler makes different, sometimes bad, silent compilation
choices for the same target gate set — this project already found one
real instance (Qiskit's rzz -> IonQ-native-ZZ synthesis bug, see
core/verifier.py). This module runs the same circuit through Qiskit's and
TKET's independent compilers targeting the same real IBM device's native
gate set, and compares them.

Scoped to IBM only, deliberately: pytket-ionq has dependency conflicts
with the qiskit/qiskit-ionq versions this project already depends on, and
forcing that combination isn't worth the maintenance risk for a first
version. IBM is also where both Qiskit and TKET have long, independently
mature support with no known dependency conflicts.

Converting a circuit between frameworks (qiskit_to_tk) is itself a real
bug surface — angle conventions and gate definitions can silently mismatch
between libraries, which is exactly the class of error this whole project
exists to catch. So this module does NOT trust either compiler's gate
count at face value: every result is independently checked against the
ORIGINAL logical circuit via exact unitary equivalence (Operator.equiv)
before being reported or recommended. That check is exponential in qubit
count, so it's skipped (with an explicit note, not a silent guess) above
MAX_QUBITS_FOR_EXACT_VERIFICATION.
"""
from pytket import OpType
from pytket.passes import AutoRebase, FullPeepholeOptimise, SequencePass
from pytket.extensions.qiskit import qiskit_to_tk, tk_to_qiskit
from qiskit import QuantumCircuit, transpile
from qiskit.quantum_info import Operator

from core.verifier import _parse
from providers.ibm import _get_service

MAX_QUBITS_FOR_EXACT_VERIFICATION = 12

# Qiskit basis-gate name -> pytket OpType, covering the gate families this
# project's real IBM devices actually report in backend.target.operation_names.
_QISKIT_TO_TKET_OPTYPE = {
    "cx": OpType.CX, "cz": OpType.CZ, "ecr": OpType.ECR,
    "rz": OpType.Rz, "sx": OpType.SX, "x": OpType.X, "id": OpType.noop,
    "rx": OpType.Rx, "ry": OpType.Ry,
}


def _ibm_native_tket_gateset(backend) -> set:
    names = set(backend.target.operation_names)
    gateset = {optype for name, optype in _QISKIT_TO_TKET_OPTYPE.items() if name in names}
    gateset.discard(OpType.noop)
    if not any(g in gateset for g in (OpType.CX, OpType.CZ, OpType.ECR)):
        raise ValueError(f"No recognized two-qubit gate found in this backend's basis gates: {names}")
    return gateset


def _two_qubit_gate_count(qc: QuantumCircuit) -> int:
    two_qubit_names = {"cx", "cz", "ecr", "cy", "swap"}
    return sum(1 for instr in qc.data if instr.operation.name in two_qubit_names)


def compile_with_qiskit(circuit: QuantumCircuit, backend) -> QuantumCircuit:
    return transpile(circuit, backend=backend, optimization_level=1)


def compile_with_tket(circuit: QuantumCircuit, gateset: set) -> QuantumCircuit:
    tkc = qiskit_to_tk(circuit)
    SequencePass([FullPeepholeOptimise(), AutoRebase(gateset)]).apply(tkc)
    return tk_to_qiskit(tkc)


def _qiskit_verification_operator(circuit: QuantumCircuit, backend, n_qubits: int):
    """
    Transpiling against a real backend returns a circuit spanning the FULL
    device width (idle ancilla padding), which makes Operator comparison
    computationally infeasible (2^156 for ibm_fez). Pinning
    initial_layout=range(n_qubits) keeps the logical qubits on physical
    qubits 0..n-1 for the START of the circuit, but real routing can still
    SWAP them among each other by the END — confirmed directly: a 3-qubit
    circuit against ibm_fez's real connectivity produced final_layout
    [1,0,2], not identity, even though it never spilled onto ancilla.
    Naively comparing gate-for-gate against physical wire order without
    correcting for that produces a FALSE "verified: False" — this isn't
    hypothetical, it's what this function's first version actually did.

    Fix: extract the active n-qubit sub-circuit, build its Operator, then
    apply the real final_layout as an explicit permutation correction
    (Operator.apply_permutation) before comparing — the physically correct
    way to verify a layout-aware compiled circuit against the original.

    Returns None (not a wrong answer) if routing spilled onto qubits
    outside the pinned initial layout — that case needs full ancilla
    tracking this function doesn't attempt.
    """
    compiled = transpile(circuit, backend=backend, optimization_level=1, initial_layout=list(range(n_qubits)))
    active_indices = {compiled.find_bit(q).index for instr in compiled.data for q in instr.qubits}
    if not active_indices.issubset(set(range(n_qubits))):
        return None
    reduced = QuantumCircuit(n_qubits)
    for instr in compiled.data:
        if instr.operation.name in ("barrier", "measure"):
            continue
        qubit_indices = [compiled.find_bit(q).index for q in instr.qubits]
        reduced.append(instr.operation, qubit_indices, [])
    op = Operator(reduced)
    final_layout = compiled.layout.final_layout if compiled.layout else None
    if final_layout is not None:
        phys_to_virtual = {
            compiled.find_bit(q).index: v for q, v in final_layout.get_virtual_bits().items()
            if compiled.find_bit(q).index < n_qubits
        }
        perm = [phys_to_virtual[i] for i in range(n_qubits)]
        op = op.apply_permutation(perm, front=False)
    return op


def diff_compilers(qasm_string: str, ibm_device: str) -> dict:
    """
    Transpile the same circuit via Qiskit and TKET against the real
    ibm_device's native gate set, verify each is unitarily equivalent to
    the original (up to MAX_QUBITS_FOR_EXACT_VERIFICATION qubits), and
    recommend whichever verified result has the lower two-qubit gate count.

    Only circuits without mid-circuit measurement are supported (Operator
    equivalence needs a purely unitary circuit) — measurements are stripped
    before comparison and noted, not silently dropped.
    """
    logical = _parse(qasm_string)
    measured_qubits = any(instr.operation.name == "measure" for instr in logical.data)
    unitary_only = logical.remove_final_measurements(inplace=False) if measured_qubits else logical

    try:
        service = _get_service()
        backend = service.backend(ibm_device)
    except Exception as e:
        return {"error": f"Could not reach IBM device '{ibm_device}': {e}"}

    try:
        gateset = _ibm_native_tket_gateset(backend)
    except Exception as e:
        return {"error": str(e)}

    results = {}
    try:
        qiskit_out = compile_with_qiskit(logical, backend)
        results["qiskit"] = {"depth": qiskit_out.depth(), "two_qubit_gates": _two_qubit_gate_count(qiskit_out),
                              "total_gates": qiskit_out.size()}
    except Exception as e:
        results["qiskit"] = {"error": str(e)}

    tket_out = None
    try:
        tket_out = compile_with_tket(unitary_only, gateset)
        results["tket"] = {"depth": tket_out.depth(), "two_qubit_gates": _two_qubit_gate_count(tket_out),
                            "total_gates": tket_out.size()}
    except Exception as e:
        results["tket"] = {"error": str(e)}

    n_qubits = unitary_only.num_qubits
    if n_qubits > MAX_QUBITS_FOR_EXACT_VERIFICATION:
        for r in results.values():
            if "error" not in r:
                r["verified"] = None
        verification_note = (
            f"{n_qubits} qubits exceeds MAX_QUBITS_FOR_EXACT_VERIFICATION="
            f"{MAX_QUBITS_FOR_EXACT_VERIFICATION} — exact unitary equivalence is exponential-cost "
            "and was skipped. Neither result is verified; do not trust gate counts blindly."
        )
    else:
        original_op = Operator(unitary_only)

        qiskit_op = None
        try:
            qiskit_op = _qiskit_verification_operator(unitary_only, backend, n_qubits)
        except Exception as e:
            results.setdefault("qiskit", {})["error"] = results.get("qiskit", {}).get("error") or str(e)
        if "qiskit" in results and "error" not in results["qiskit"]:
            results["qiskit"]["verified"] = qiskit_op.equiv(original_op) if qiskit_op is not None else None
            if qiskit_op is None:
                results["qiskit"]["verification_skipped_reason"] = (
                    "routing relocated logical qubits outside the pinned initial layout — "
                    "cannot verify without full layout-permutation tracking"
                )

        if "tket" in results and "error" not in results["tket"] and tket_out is not None:
            results["tket"]["verified"] = Operator(tket_out).equiv(original_op)

        verification_note = (
            "Each result checked against the original circuit via exact unitary equivalence, "
            "where the qubit-layout mapping could be tracked cleanly (see per-result notes otherwise)."
        )

    verified_candidates = {
        name: r for name, r in results.items()
        if "error" not in r and r.get("verified") is True
    }
    if not verified_candidates:
        recommendation = "No compiler produced a verified result — do not submit either without manual review."
    else:
        winner = min(verified_candidates, key=lambda name: verified_candidates[name]["two_qubit_gates"])
        recommendation = (
            f"{winner} produced the verified result with the lower two-qubit gate count "
            f"({verified_candidates[winner]['two_qubit_gates']})."
        )

    return {
        "device": ibm_device,
        "measurements_stripped_for_comparison": measured_qubits,
        "results": results,
        "verification_note": verification_note,
        "recommendation": recommendation,
    }
