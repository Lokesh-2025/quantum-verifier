"""
Pre-submission structural analysis of a PARAMETERIZED circuit, using
PennyLane's autodiff (qml.grad) -- genuinely new: not "run on PennyLane's
simulator instead of Qiskit's," but using gradients to find out something
about the circuit's STRUCTURE before it's even eligible to be turned into
QASM and handed to verify().

Why this can't live inside verify()'s pipeline: QASM is the final,
fully-bound submission format -- every gate angle is already a concrete
number by the time a circuit is QASM, there is nothing left to
differentiate with respect to. Gradients are only meaningful on the
original, still-parameterized circuit DEFINITION, evaluated at a specific
point, one lifecycle stage earlier than verify()'s qasm_string entrypoint.

Deliberately outside the QuantumBackendAdapter interface and NOT gated by
a supports_* flag -- same reasoning providers/ibm.py's best_qubits already
established in this codebase: this isn't a per-provider capability with
real variance across vendors, it's a circuit-analysis capability that
happens to use PennyLane's autodiff as its implementation detail, and it
isn't tied to whichever provider a circuit is eventually headed for.

Real, empirically-confirmed detail: `pennylane.numpy` (not plain numpy)
is required for the `requires_grad=True` array flag qml.grad depends on
-- plain numpy.array() has no such keyword.
"""
import pennylane as qml
import pennylane.numpy as pnp


def analyze_gradients(
    build_circuit_fn,
    params,
    n_qubits: int,
    observable=None,
    barren_plateau_gradient_norm_threshold: float = 1e-2,
    near_zero_gradient_threshold: float = 1e-3,
) -> dict:
    """
    Args:
        build_circuit_fn: callable(params, wires) -> None; a PennyLane
            circuit-defining function that applies gates using the given
            parameter values on the given wires. NOT a QASM string -- QASM
            has no unbound parameters left to differentiate.
        params: array-like, the parameter point to evaluate gradients at.
        n_qubits: number of wires the circuit uses.
        observable: optional PennyLane observable for the cost function.
            Defaults to qml.PauliZ(0) -- a generic proxy. A caller with a
            specific claimed cost/observable should pass their own.
        barren_plateau_gradient_norm_threshold: below this gradient-norm
            value, flags a likely barren plateau at this parameter point.
        near_zero_gradient_threshold: individual parameters whose gradient
            magnitude falls below this are flagged as candidate
            redundant/cancelling gates.

    Returns a dict with per-parameter gradients, gradient norm, barren-
    plateau risk, and near-zero-gradient parameter indices.
    """
    if observable is None:
        observable = qml.PauliZ(0)

    dev = qml.device("default.qubit", wires=n_qubits)

    @qml.qnode(dev)
    def cost(p):
        build_circuit_fn(p, wires=range(n_qubits))
        return qml.expval(observable)

    p = pnp.array(params, requires_grad=True)
    grad = qml.grad(cost)(p)
    grad = pnp.atleast_1d(grad)
    grad_norm = float(pnp.linalg.norm(grad))

    near_zero_params = [i for i, g in enumerate(grad) if abs(float(g)) < near_zero_gradient_threshold]
    barren_plateau_risk = grad_norm < barren_plateau_gradient_norm_threshold

    return {
        "n_params": len(p),
        "gradient_norm": round(grad_norm, 6),
        "per_parameter_gradient": [round(float(g), 6) for g in grad],
        "near_zero_gradient_params": near_zero_params,
        "barren_plateau_risk": barren_plateau_risk,
        "verdict": (
            f"Gradient norm ({grad_norm:.2e}) is below the barren-plateau threshold "
            f"({barren_plateau_gradient_norm_threshold:.0e}) -- training/optimizing this "
            "ansatz at this parameter point is likely to make negligible progress; "
            "this is a structural property of the circuit at this point, not a claim "
            "about hardware noise."
            if barren_plateau_risk else
            f"Gradient norm ({grad_norm:.2e}) is healthy -- {len(near_zero_params)}/{len(p)} "
            "individual parameter(s) have near-zero gradient, worth checking if any "
            "correspond to redundant/cancelling gate pairs."
        ),
    }


# Bounded gate vocabulary for the JSON-describable spec below -- same
# vocabulary shape as adapters/cudaq_adapter.py's circuit.data translator,
# since MCP tool calls can only carry JSON-serializable arguments, not a
# Python callable -- `build_circuit_fn` above works for direct in-process
# Python callers (tests, other core/ modules) but cannot cross an MCP
# tool boundary. This spec format is what actually can.
_SPEC_1Q_PARAM = {"rx", "ry", "rz"}
_SPEC_1Q_FIXED = {"h", "x", "y", "z"}
_SPEC_2Q_FIXED = {"cnot", "cz"}


def _build_circuit_fn_from_spec(gate_spec: list):
    """
    gate_spec: list of gate instruction dicts, one of:
      {"gate": "rx"|"ry"|"rz", "wire": int, "param_index": int}
      {"gate": "h"|"x"|"y"|"z", "wire": int}
      {"gate": "cnot"|"cz", "wires": [control, target]}
    Returns a callable(params, wires) suitable for analyze_gradients.
    """
    for instr in gate_spec:
        name = instr.get("gate", "").lower()
        if name not in (_SPEC_1Q_PARAM | _SPEC_1Q_FIXED | _SPEC_2Q_FIXED):
            raise ValueError(
                f"Unsupported gate '{name}' in gate_spec -- expected one of "
                f"{sorted(_SPEC_1Q_PARAM | _SPEC_1Q_FIXED | _SPEC_2Q_FIXED)}."
            )

    def build(params, wires):
        for instr in gate_spec:
            name = instr["gate"].lower()
            if name in _SPEC_1Q_PARAM:
                getattr(qml, name.upper())(params[instr["param_index"]], wires=instr["wire"])
            elif name in _SPEC_1Q_FIXED:
                getattr(qml, name.capitalize())(wires=instr["wire"])
            elif name in _SPEC_2Q_FIXED:
                gate_cls = qml.CNOT if name == "cnot" else qml.CZ
                gate_cls(wires=instr["wires"])

    return build


def analyze_gradients_from_spec(
    gate_spec: list,
    params: list,
    n_qubits: int,
    observable_wire: int = 0,
    barren_plateau_gradient_norm_threshold: float = 1e-2,
    near_zero_gradient_threshold: float = 1e-3,
) -> dict:
    """
    JSON-describable entrypoint for analyze_gradients, for MCP tool
    exposure (analyze_gradients itself takes a raw Python callable, which
    cannot cross an MCP tool boundary -- MCP arguments are JSON-only).
    Builds the circuit-defining function from gate_spec, then delegates.
    """
    try:
        build_fn = _build_circuit_fn_from_spec(gate_spec)
    except ValueError as e:
        return {"error": str(e)}
    return analyze_gradients(
        build_fn, params, n_qubits, observable=qml.PauliZ(observable_wire),
        barren_plateau_gradient_norm_threshold=barren_plateau_gradient_norm_threshold,
        near_zero_gradient_threshold=near_zero_gradient_threshold,
    )
