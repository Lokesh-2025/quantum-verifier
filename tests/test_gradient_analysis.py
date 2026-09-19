"""
Tests for core/gradient_analysis.py. The barren-plateau test uses a
RELATIVE trend (gradient norm shrinking substantially as qubit count
grows with a global observable) rather than an absolute threshold-crossing
assertion on one fixed random seed -- confirmed empirically that a single
absolute threshold is fragile/seed-dependent (barren plateaus are a
statistical property of the ensemble, not a deterministic property of any
one random draw), while the relative shrinking trend itself is real and
reproducible: gradient norm dropped ~40x (2.2 -> 0.054) going from 2 to 12
qubits with the same seed and a global observable in real testing.
"""
import pennylane as qml
import numpy as np

from core.gradient_analysis import analyze_gradients, analyze_gradients_from_spec


def test_cancelling_gate_pair_flagged_near_zero_gradient():
    """
    RX(p0) then RX(-p0) on the same qubit is an exact no-op regardless of
    p0's value -- its gradient must be (near) exactly zero, while a
    genuinely active parameter on a different qubit shows a real gradient.
    Observable is PauliZ(1), not the default PauliZ(0), specifically so
    the active parameter's effect isn't masked by CNOT's control-qubit
    invariance (confirmed directly: PauliZ(0) as the observable made BOTH
    parameters show zero gradient here, for a real structural reason --
    the control qubit's own Z expectation is unaffected by what happens
    to the target -- not a bug, but the wrong observable to demonstrate
    this with).
    """
    def build(params, wires):
        qml.RX(params[0], wires=0)
        qml.RX(-params[0], wires=0)  # cancelling pair -- net no-op
        qml.RY(params[1], wires=1)   # genuinely active
        qml.CNOT(wires=[0, 1])

    result = analyze_gradients(build, [0.5, 0.3], n_qubits=2, observable=qml.PauliZ(1))
    assert 0 in result["near_zero_gradient_params"]
    assert 1 not in result["near_zero_gradient_params"]
    assert abs(result["per_parameter_gradient"][1]) > 1e-2


def test_gradient_norm_shrinks_substantially_with_qubit_count_for_a_global_observable():
    """
    The real, reproducible barren-plateau trend (McClean et al.): a random
    hardware-efficient ansatz's gradient norm, measured against a GLOBAL
    multi-qubit observable, shrinks substantially as qubit count grows.
    Same seed used throughout so this is a controlled, deterministic
    comparison, not a threshold crossing dependent on random luck.
    """
    def make_ansatz(n_qubits, n_layers):
        def ansatz(params, wires):
            idx = 0
            for _ in range(n_layers):
                for w in wires:
                    qml.RX(params[idx], wires=w); idx += 1
                    qml.RZ(params[idx], wires=w); idx += 1
                for w in range(len(list(wires)) - 1):
                    qml.CZ(wires=[w, w + 1])
        return ansatz

    n_layers = 8
    norms = {}
    for n_qubits in (2, 12):
        np.random.seed(0)
        obs = qml.PauliZ(0)
        for w in range(1, n_qubits):
            obs = obs @ qml.PauliZ(w)
        n_params = n_layers * n_qubits * 2
        params = np.random.uniform(0, 2 * np.pi, n_params)
        result = analyze_gradients(make_ansatz(n_qubits, n_layers), params,
                                    n_qubits=n_qubits, observable=obs)
        norms[n_qubits] = result["gradient_norm"]

    # Real, reproducible effect: substantially smaller at 12 qubits than at 2.
    assert norms[12] < norms[2] / 10


def test_analyze_gradients_from_spec_matches_direct_callable_version():
    """
    The JSON-describable entrypoint (for MCP tool exposure, since a raw
    Python callable cannot cross an MCP tool boundary) must produce the
    same result as the equivalent direct analyze_gradients call.
    """
    def build(params, wires):
        qml.RX(params[0], wires=0)
        qml.RX(params[1], wires=0)
        qml.RY(params[2], wires=1)
        qml.CNOT(wires=[0, 1])

    params = [0.5, -0.5, 0.3]
    direct = analyze_gradients(build, params, n_qubits=2, observable=qml.PauliZ(1))

    spec = [
        {"gate": "rx", "wire": 0, "param_index": 0},
        {"gate": "rx", "wire": 0, "param_index": 1},
        {"gate": "ry", "wire": 1, "param_index": 2},
        {"gate": "cnot", "wires": [0, 1]},
    ]
    from_spec = analyze_gradients_from_spec(spec, params, n_qubits=2, observable_wire=1)

    assert from_spec["gradient_norm"] == direct["gradient_norm"]
    assert from_spec["near_zero_gradient_params"] == direct["near_zero_gradient_params"]


def test_analyze_gradients_from_spec_reports_a_clear_error_for_an_unsupported_gate():
    spec = [{"gate": "not_a_real_gate", "wire": 0}]
    result = analyze_gradients_from_spec(spec, [0.1], n_qubits=1)
    assert "error" in result
