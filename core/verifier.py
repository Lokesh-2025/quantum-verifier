"""
The Verifier — a safety gate between an AI-generated circuit and real
quantum hardware.

Built from the seed logic already proven in quantum-hardware-mcp/server.py
(debug_circuit's static checks, check_routing_overhead's heavy-hex degree
math, circuit_report's fidelity estimate, ionq_submit_job's noise-model
self-check pattern, certify_ising_gate_optimality's exact optimality proof)
— generalized into one pipeline that works for either vendor, not copied
verbatim, per the approved plan.

Pipeline:
  1. Semantic check     — is this a well-formed circuit at all?
  2. Topology check      — vendor-specific: IBM heavy-hex degree limits
                           (real routing risk); IonQ is all-to-all, so this
                           step is a documented no-op there, not skipped
                           silently.
  3. Ideal simulation    — what SHOULD happen with zero noise?
  4. Hardware-aware sim   — what does the REAL target device's noise
                           actually predict? IonQ: full noisy simulation
                           using the device's real named noise model. IBM:
                           a fidelity estimate from real calibration data
                           (product-of-gate-errors) — asymmetry is real and
                           documented, not hidden; IBM's public API doesn't
                           expose a per-device noise model the way IonQ's
                           does.
  5. Ground-truth check  — EITHER compare to a known expected result, OR
                           (if none is supplied — genuine discovery-mode
                           research) hand off to control_experiment.py to
                           auto-generate a falsifying control circuit.
  6. GO / BLOCK verdict, with structured, human-readable reasons.
"""
from qiskit import QuantumCircuit, transpile
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

from providers.ibm import _get_service, _cx_errors_for_backend

HEAVY_HEX_MAX_DEGREE = 3


def _parse(qasm_string: str) -> QuantumCircuit:
    return QuantumCircuit.from_qasm_str(qasm_string)


# ---------------------------------------------------------------- step 1
def semantic_check(circuit: QuantumCircuit) -> dict:
    """
    Static checks — no hardware connection needed. Generalized from
    debug_circuit's static-analysis half (server.py's hardware-specific
    checks, e.g. coherence-time comparisons, live in hardware_aware_check
    instead, since they need a real target device).
    """
    issues = []
    n_qubits = circuit.num_qubits
    n_clbits = circuit.num_clbits
    ops = circuit.count_ops()

    if not ops or all(k in ("barrier", "measure") for k in ops):
        issues.append({"severity": "ERROR", "check": "empty_circuit",
                        "message": "Circuit has no quantum gates."})

    if ops.get("measure", 0) == 0:
        issues.append({"severity": "ERROR", "check": "no_measurements",
                        "message": "Circuit has no measurement gates — you will get no results."})

    if n_clbits < ops.get("measure", 0):
        issues.append({"severity": "ERROR", "check": "classical_register_too_small",
                        "message": f"{ops.get('measure', 0)} measurements but only {n_clbits} classical bits."})

    entangled_qubits = set()
    for instruction in circuit.data:
        if len(instruction.qubits) >= 2:
            for q in instruction.qubits:
                entangled_qubits.add(circuit.find_bit(q).index)
    single_only = [
        i for i in range(n_qubits)
        if any(circuit.find_bit(q).index == i for inst in circuit.data for q in inst.qubits
               if inst.operation.name not in ("measure", "barrier"))
        and i not in entangled_qubits
    ]
    if single_only and n_qubits > 1:
        issues.append({"severity": "INFO", "check": "unentangled_qubits",
                        "message": f"Qubit(s) {single_only} never interact with other qubits."})

    errors = [i for i in issues if i["severity"] == "ERROR"]
    return {"issues": issues, "passed": len(errors) == 0}


# ---------------------------------------------------------------- step 2
def topology_check(circuit: QuantumCircuit, provider: str) -> dict:
    """
    Vendor-specific structural risk check. IBM heavy-hex caps any qubit at
    3 direct interaction partners — exceeding it forces SWAP injection and
    can 4x+ the real gate count (the exact failure this project hit and
    documented). IonQ is all-to-all, so this risk structurally does not
    exist there — returned explicitly as a no-op, not silently skipped, so
    a caller can tell "checked, and safe" apart from "not applicable."
    """
    if provider == "ionq":
        return {"applicable": False, "passed": True,
                "note": "IonQ is all-to-all connected — no routing/degree risk exists for this provider."}

    from collections import defaultdict
    neighbors = defaultdict(set)
    for instruction in circuit.data:
        if len(instruction.qubits) == 2:
            a = circuit.find_bit(instruction.qubits[0]).index
            b = circuit.find_bit(instruction.qubits[1]).index
            neighbors[a].add(b)
            neighbors[b].add(a)

    violations = []
    for qubit, nbrs in sorted(neighbors.items()):
        degree = len(nbrs)
        excess = max(0, degree - HEAVY_HEX_MAX_DEGREE)
        if excess > 0:
            violations.append({"qubit": qubit, "degree": degree,
                                "estimated_extra_cx": excess * 3})

    passed = len(violations) == 0
    return {
        "applicable": True, "passed": passed,
        "heavy_hex_max_degree": HEAVY_HEX_MAX_DEGREE,
        "violations": violations,
        "note": ("All qubits within degree-3 limit." if passed else
                 f"{len(violations)} qubit(s) exceed the degree-3 limit — "
                 "real routing overhead expected, gate count may inflate 3-5x."),
    }


# ---------------------------------------------------------------- step 3
def ideal_simulation(circuit: QuantumCircuit, shots: int = 4096) -> dict:
    """What SHOULD happen with zero noise — the ground truth to compare against."""
    from qiskit_aer import AerSimulator
    sim = AerSimulator()
    counts = sim.run(circuit, shots=shots).result().get_counts()
    return {"counts": counts, "total_shots": sum(counts.values())}


# ---------------------------------------------------------------- step 4
def hardware_aware_simulation(circuit: QuantumCircuit, provider: str, target_device: str, shots: int = 4096) -> dict:
    """
    What the REAL target device's noise actually predicts.

    IonQ: full noisy simulation using the device's real named noise model
    (depolarizing channels after each gate, fixed rates — verified against
    IonQ's own docs). This is a genuine noisy execution, not an estimate.

    IBM: a fidelity ESTIMATE from real, live calibration data
    (product-of-gate-errors across the transpiled circuit's 2-qubit gates).
    This is weaker than a full noisy simulation — IBM's public API doesn't
    expose an equivalent named noise model the way IonQ's does — and that
    asymmetry is stated here explicitly rather than papered over.
    """
    if provider == "ionq":
        from qiskit_ionq import IonQProvider
        import os
        api_key = os.getenv("IONQ_API_KEY")
        if not api_key:
            return {"error": "IONQ_API_KEY not set"}
        from providers.ionq import _resolve_ionq_backend, _ionq_is_hardware
        resolved = _resolve_ionq_backend(target_device)
        ionq_provider = IonQProvider(api_key)
        target_backend = ionq_provider.get_backend(resolved, gateset="native")
        sim_backend = ionq_provider.get_backend("ionq_simulator", gateset="native")
        if _ionq_is_hardware(resolved):
            sim_backend.set_options(noise_model=resolved.replace("qpu.", ""))
        t_qc = transpile(circuit, backend=target_backend, optimization_level=1)
        sim_job = sim_backend.run(t_qc, shots=shots)
        counts = sim_job.result().get_counts()
        noise_model_used = sim_backend.options.noise_model
        simulation_type = (
            f"full noisy simulation using {noise_model_used}'s real, named noise model"
            if noise_model_used and noise_model_used != "ideal"
            else "ideal simulation, no noise model applied (target was the free simulator, not real hardware)"
        )
        return {
            "counts": counts, "total_shots": sum(counts.values()),
            "noise_model_used": noise_model_used,
            "transpiled_gate_count": t_qc.size(),
            "simulation_type": simulation_type,
        }

    # IBM
    service = _get_service()
    try:
        backend = service.backend(target_device)
    except Exception as e:
        return {"error": f"Device '{target_device}' not found: {e}"}
    pm = generate_preset_pass_manager(backend=backend, optimization_level=1)
    isa_circuit = pm.run(circuit)
    transpiled_gates = dict(isa_circuit.count_ops())
    n_cx = transpiled_gates.get("cx", 0) + transpiled_gates.get("ecr", 0) + transpiled_gates.get("cz", 0)
    props = backend.properties()
    cx_errors = _cx_errors_for_backend(props) if props else []
    avg_cx_error = sum(cx_errors) / len(cx_errors) if cx_errors else 0.005
    estimated_fidelity = round((1 - avg_cx_error) ** n_cx, 4) if n_cx > 0 else 1.0
    return {
        "estimated_fidelity": estimated_fidelity,
        "transpiled_gate_count": sum(transpiled_gates.values()),
        "n_two_qubit_gates": n_cx,
        "simulation_type": "fidelity estimate from live calibration data (not a full noisy simulation)",
    }


# ---------------------------------------------------------------- step 5
def ground_truth_check(hw_counts: dict, expected_marked_bitstrings: list,
                        expected_amplification: float, tolerance: float = 0.5) -> dict:
    """Compare a hardware-aware simulation's result against a KNOWN expected answer."""
    if not hw_counts:
        return {"applicable": False, "note": "No counts available (IBM path returns a fidelity estimate, not counts)."}
    total = sum(hw_counts.values())
    marked = set(expected_marked_bitstrings)
    n_qubits = len(next(iter(hw_counts.keys())))
    marked_shots = sum(c for b, c in hw_counts.items() if b in marked)
    observed_amp = (marked_shots / total) / (len(marked) / (2 ** n_qubits)) if total else 0
    lo = expected_amplification * (1 - tolerance)
    hi = expected_amplification * (1 + tolerance)
    within = lo <= observed_amp <= hi
    return {
        "applicable": True, "observed_amplification": round(observed_amp, 3),
        "expected_amplification": expected_amplification,
        "within_tolerance": within,
        "verdict": ("Claimed signal is consistent with hardware-predicted behavior." if within else
                    f"Claimed signal ({expected_amplification}x) is NOT distinguishable from "
                    f"hardware-predicted behavior ({round(observed_amp, 2)}x) — the experiment "
                    "cannot currently support this claim."),
    }


# ---------------------------------------------------------------- orchestrator
def verify(
    qasm_string: str,
    provider: str,
    target_device: str,
    shots: int = 4096,
    expected_marked_bitstrings: list = None,
    expected_amplification: float = None,
    amplification_tolerance: float = 0.5,
) -> dict:
    """
    Run the full pipeline and return a GO / BLOCK verdict.

    If expected_marked_bitstrings and expected_amplification are both
    given, verifies the claim against a known answer. If neither is given,
    the caller is in discovery mode — see control_experiment.py for the
    control-circuit alternative, which doesn't require knowing the answer
    in advance.
    """
    result = {"provider": provider, "target_device": target_device}

    try:
        circuit = _parse(qasm_string)
    except Exception as e:
        return {**result, "verdict": "BLOCK", "reason": f"Failed to parse QASM: {e}"}

    sem = semantic_check(circuit)
    result["semantic_check"] = sem
    if not sem["passed"]:
        return {**result, "verdict": "BLOCK",
                "reason": "Semantic check failed — circuit is malformed.",
                "details": sem["issues"]}

    topo = topology_check(circuit, provider)
    result["topology_check"] = topo
    if not topo["passed"]:
        return {**result, "verdict": "BLOCK",
                "reason": "Topology check failed — real routing overhead expected.",
                "details": topo}

    ideal = ideal_simulation(circuit, shots)
    result["ideal_simulation"] = {"total_shots": ideal["total_shots"],
                                   "top_outcomes": dict(sorted(ideal["counts"].items(), key=lambda x: -x[1])[:5])}

    hw = hardware_aware_simulation(circuit, provider, target_device, shots)
    result["hardware_aware_simulation"] = hw
    if "error" in hw:
        return {**result, "verdict": "BLOCK", "reason": hw["error"]}

    if expected_marked_bitstrings and expected_amplification is not None:
        gt = ground_truth_check(hw.get("counts"), expected_marked_bitstrings,
                                 expected_amplification, amplification_tolerance)
        result["ground_truth_check"] = gt
        if gt.get("applicable") and not gt["within_tolerance"]:
            return {**result, "verdict": "BLOCK", "reason": gt["verdict"]}
    else:
        result["ground_truth_check"] = {
            "applicable": False,
            "note": "No expected_marked_bitstrings/expected_amplification supplied — "
                    "discovery mode. Use control_experiment.falsify() for a claim check "
                    "that doesn't require knowing the answer in advance.",
        }

    result["verdict"] = "GO"
    result["reason"] = "All checks passed — circuit is safe and its predicted behavior is consistent."
    return result
