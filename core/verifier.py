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
import math

from qiskit import QuantumCircuit, transpile
from qiskit.circuit import Parameter
from qiskit.circuit.library import RZZGate
from qiskit.circuit.equivalence_library import SessionEquivalenceLibrary as _sel
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

from providers.ibm import _get_service, _cx_errors_for_backend

HEAVY_HEX_MAX_DEGREE = 3

# Qiskit's transpiler has no direct equivalence between RZZGate (radians) and
# IonQ's native ZZGate (turns), even though they are the exact same physical
# gate — RZZ(theta) = exp(-i*theta/2 * Z@Z) and native ZZ(phi) = exp(-i*pi*phi
# * Z@Z), so phi = theta/(2*pi) is an EXACT match, verified via Operator
# comparison (identical, not just up to global phase). Without this
# registered, the transpiler falls back to general two-qubit unitary
# synthesis: a single rzz between two H gates transpiled to 2 native zz
# gates (should be 1) plus ~39 extraneous single-qubit gpi/gpi2 gates. That
# silently produced a wrong "hardware-aware simulation" for any circuit
# containing rzz — caught via the free-simulator control-arm check for the
# Forte angle-error experiment before it ever reached real hardware.
#
# RXX and RYY were checked for the same pattern and do NOT have it: an
# isolated rxx/ryy against IonQ's real native basis showed extra gpi/gpi2
# gates too, but that's the real, unavoidable cost of basis-change gates
# (H, S) on this hardware -- confirmed directly, a bare isolated H alone
# already costs 2 gpi2 + 1 gpi, and RXX needs 4 of them, exactly accounting
# for the total seen. Not a missing equivalence; no fix needed or applied.
def _register_ionq_native_equivalences():
    try:
        from qiskit_ionq.ionq_gates import ZZGate as _IonQZZGate
    except ImportError:
        return
    theta = Parameter("theta")
    equiv = QuantumCircuit(2)
    equiv.append(_IonQZZGate(theta / (2 * math.pi)), [0, 1])
    _sel.add_equivalence(RZZGate(theta), equiv)


_register_ionq_native_equivalences()

# IonQ's native ZZ gate is only valid for |theta_turns| <= 0.25 (a quarter
# turn) -- confirmed via a real IonQ API rejection ("angle must be >= -0.25")
# when the E1_RING circuit's large rzz angles (>pi/2 radians) hit the 1:1
# equivalence above. Splitting into N chained smaller RZZ applications is
# mathematically EXACT, not an approximation: ZZ generators on the same
# qubit pair commute, so N reps of RZZ(theta/N) = RZZ(theta) exactly (the
# same identity the angle-error experiment's own protocol relies on).
IONQ_NATIVE_ZZ_MAX_TURNS = 0.25


def _decompose_large_angle_rzz(circuit: QuantumCircuit) -> QuantumCircuit:
    new_qc = circuit.copy_empty_like()
    for instruction in circuit.data:
        if instruction.operation.name == "rzz":
            theta = float(instruction.operation.params[0])
            theta_turns = theta / (2 * math.pi)
            n_chunks = max(1, math.ceil(abs(theta_turns) / IONQ_NATIVE_ZZ_MAX_TURNS))
            for _ in range(n_chunks):
                new_qc.rzz(theta / n_chunks, instruction.qubits[0], instruction.qubits[1])
        else:
            new_qc.append(instruction.operation, instruction.qubits, instruction.clbits)
    return new_qc


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
GATE_INFLATION_MAX_RATIO = 8.0
TWO_QUBIT_GATE_INFLATION_MAX_RATIO = 1.5

# Fixed-angle native two-qubit gates (cz, cx, ecr) cannot directly implement
# an arbitrary-angle rzz -- the standard, minimal, EXACT construction needs
# 2 of them (sandwich a single-qubit RZ between two copies), confirmed
# directly: an isolated rzz transpiled against ibm_fez's real cz-native
# basis produces exactly 2 cz, not more, not fewer. That's the correct
# answer, not inflation -- IonQ's native ZZ gate is TUNABLE (any angle
# directly), so 1:1 is the right bar there, but holding CZ/CX/ECR-native
# devices to that same 1.5x threshold flags a real device's optimal,
# unavoidable result as a false failure. Confirmed on real ibm_fez data:
# both e1_single and e1_ring transpiled at exactly 2.0x, consistently.
FIXED_ANGLE_NATIVE_TWO_QUBIT_GATES = {"cz", "cx", "ecr"}
FIXED_ANGLE_TWO_QUBIT_GATE_INFLATION_MAX_RATIO = 2.5


def _count_two_qubit_gates(circuit: QuantumCircuit) -> int:
    return sum(1 for instr in circuit.data if len(instr.qubits) == 2)


def gate_synthesis_check(circuit: QuantumCircuit, transpiled: QuantumCircuit) -> dict:
    """
    Catches the real-world form of "wrong native gate family": a circuit
    whose gates don't map cleanly onto the target's native gateset, forcing
    the transpiler to fall back to expensive general unitary re-synthesis
    instead of a direct translation. Same spirit as topology_check flagging
    heavy-hex degree violations — this is the gate-basis equivalent.

    This is exactly the failure mode this project hit for real: a missing
    RZZ->native-ZZ equivalence caused a single rzz to transpile into 2
    native two-qubit gates (should be 1) plus ~39 extraneous single-qubit
    gates, silently producing a "hardware-aware simulation" of the wrong
    circuit. Registering the correct equivalence (see the module-level
    _register_ionq_native_equivalences call) fixes IonQ's RZZ case
    specifically; this check exists to catch the *general* class for any
    gate/target combination that hits the same kind of gap.

    The "correct" ratio depends on whether the target's native two-qubit
    gate is tunable or fixed-angle -- IonQ's native ZZ gate takes any
    angle directly, so 1:1 is the right bar there. IBM's native CZ (and
    CX/ECR) are fixed-angle, so an arbitrary-angle rzz needs exactly 2 of
    them (sandwich a single-qubit RZ between two copies) -- that's the
    real, unavoidable minimum, confirmed directly against ibm_fez's real
    basis (an isolated rzz transpiles to exactly 2 cz, not more). Holding
    fixed-angle-native devices to the same 1.5x bar as tunable ones would
    flag a device's correct, optimal result as a false failure.
    """
    logical_ops = {k: v for k, v in circuit.count_ops().items() if k not in ("measure", "barrier")}
    transpiled_ops = {k: v for k, v in transpiled.count_ops().items() if k not in ("measure", "barrier")}
    logical_total = sum(logical_ops.values())
    transpiled_total = sum(transpiled_ops.values())
    inflation_ratio = (transpiled_total / logical_total) if logical_total else 1.0

    logical_2q = _count_two_qubit_gates(circuit)
    transpiled_2q = _count_two_qubit_gates(transpiled)
    two_qubit_inflation_ratio = (transpiled_2q / logical_2q) if logical_2q else 1.0

    transpiled_2q_gate_names = {
        instr.operation.name for instr in transpiled.data if len(instr.qubits) == 2
    }
    uses_fixed_angle_native_gate = bool(transpiled_2q_gate_names & FIXED_ANGLE_NATIVE_TWO_QUBIT_GATES)
    two_qubit_threshold = (
        FIXED_ANGLE_TWO_QUBIT_GATE_INFLATION_MAX_RATIO if uses_fixed_angle_native_gate
        else TWO_QUBIT_GATE_INFLATION_MAX_RATIO
    )

    violations = []
    if inflation_ratio > GATE_INFLATION_MAX_RATIO:
        violations.append({
            "check": "total_gate_inflation",
            "logical_gate_count": logical_total, "transpiled_gate_count": transpiled_total,
            "inflation_ratio": round(inflation_ratio, 2),
            "message": f"Transpiled gate count is {inflation_ratio:.1f}x the logical count "
                       f"(threshold {GATE_INFLATION_MAX_RATIO}x) — likely a missing direct "
                       "equivalence to the target's native gateset, not real routing overhead.",
        })
    if logical_2q and two_qubit_inflation_ratio > two_qubit_threshold:
        violations.append({
            "check": "two_qubit_gate_inflation",
            "logical_two_qubit_gates": logical_2q, "transpiled_two_qubit_gates": transpiled_2q,
            "inflation_ratio": round(two_qubit_inflation_ratio, 2),
            "message": f"{logical_2q} logical two-qubit gate(s) became {transpiled_2q} native "
                       f"two-qubit gate(s) ({two_qubit_inflation_ratio:.1f}x, threshold "
                       f"{two_qubit_threshold}x for {'fixed-angle' if uses_fixed_angle_native_gate else 'tunable'} "
                       f"native gates {sorted(transpiled_2q_gate_names) or '(none)'}) — likely a missing direct "
                       "equivalence to the target's native gateset, not the expected minimal decomposition.",
        })

    return {
        "passed": len(violations) == 0,
        "logical_gate_count": logical_total, "transpiled_gate_count": transpiled_total,
        "inflation_ratio": round(inflation_ratio, 2),
        "logical_two_qubit_gates": logical_2q, "transpiled_two_qubit_gates": transpiled_2q,
        "two_qubit_gate_inflation_threshold_used": two_qubit_threshold,
        "native_two_qubit_gates": sorted(transpiled_2q_gate_names),
        "violations": violations,
    }


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
        try:
            resolved = _resolve_ionq_backend(target_device)
            ionq_provider = IonQProvider(api_key)
            # Transpiling against the bare "ionq_simulator" target silently picks
            # its DEFAULT native gateset, which is the legacy Aria-only MS gate,
            # not Forte's zz -- the exact trap estimate_ionq_gates/estimate_ionq_cost
            # already document and avoid by defaulting to forte-1's real target.
            # A gate-count/structure check needs a REAL device's native gateset
            # even when no noise model will be applied (no specific hardware
            # requested), so target the transpile at forte-1 in that case while
            # still executing on the free simulator with no noise.
            transpile_target_name = "qpu.forte-1" if resolved == "ionq_simulator" else resolved
            target_backend = ionq_provider.get_backend(transpile_target_name, gateset="native")
            sim_backend = ionq_provider.get_backend("ionq_simulator", gateset="native")
            if _ionq_is_hardware(resolved):
                sim_backend.set_options(noise_model=resolved.replace("qpu.", ""))
            # Split any rzz beyond the native gate's valid angle range BEFORE
            # transpiling, so the 1:1 equivalence applies cleanly to every chunk
            # instead of the transpiler rejecting/mis-synthesizing an out-of-range angle.
            decomposed_circuit = _decompose_large_angle_rzz(circuit)
            t_qc = transpile(decomposed_circuit, backend=target_backend, optimization_level=1)
            sim_job = sim_backend.run(t_qc, shots=shots)
            counts = sim_job.result().get_counts()
        except Exception as e:
            return {"error": f"IonQ hardware-aware simulation failed: {e}"}
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
            "gate_synthesis_check": gate_synthesis_check(decomposed_circuit, t_qc),
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

    # Real noisy simulation, not just a product-of-errors estimate: build an
    # Aer noise model from this backend's ACTUAL live calibration data
    # (qiskit_aer.noise.NoiseModel.from_backend), then run it locally, for
    # free -- no QPU time spent. This is the same shape as IonQ's free-
    # simulator-with-a-real-named-noise-model path, closing a real gap:
    # falsify_claim needs actual counts to compare real-vs-control, and an
    # estimate alone can't provide that. Previously IBM had no counts here
    # at all, so falsify_claim only worked on the IonQ path -- confirmed
    # directly against the code, not assumed.
    counts, total_shots, noisy_sim_error = None, None, None
    try:
        from qiskit_aer import AerSimulator
        from qiskit_aer.noise import NoiseModel
        noise_model = NoiseModel.from_backend(backend)
        noisy_backend = AerSimulator(noise_model=noise_model, coupling_map=backend.coupling_map,
                                      basis_gates=noise_model.basis_gates)
        noisy_job = noisy_backend.run(isa_circuit, shots=shots)
        counts = noisy_job.result().get_counts()
        total_shots = sum(counts.values())
    except Exception as e:
        noisy_sim_error = str(e)

    return {
        "counts": counts, "total_shots": total_shots,
        "estimated_fidelity": estimated_fidelity,
        "transpiled_gate_count": sum(transpiled_gates.values()),
        "n_two_qubit_gates": n_cx,
        "simulation_type": (
            "full noisy simulation using a local Aer noise model built from this backend's real, "
            "live calibration data (qiskit_aer NoiseModel.from_backend) -- plus a calibration-based "
            "fidelity estimate for backward compatibility"
            if counts is not None else
            f"fidelity estimate only -- real noisy simulation failed: {noisy_sim_error}"
        ),
        "gate_synthesis_check": gate_synthesis_check(circuit, isa_circuit),
    }


def cross_check_fidelity_estimate(circuit: QuantumCircuit, hw_result: dict, shots: int = 4096) -> dict:
    """
    Independent second opinion on IBM's noise estimate, added 2026-08-24 —
    the same "don't trust one method, check it against an independent one"
    principle falsify_claim already applies to circuits (real vs control)
    and diff_compilers already applies across compilers (Qiskit vs TKET),
    applied one level deeper: to hardware_aware_simulation's own noise
    estimate. That function already computes TWO independent signals for
    IBM (an analytical product-of-gate-errors estimate, and a full local
    noisy Aer simulation) but never compared them to each other — this
    does that comparison.

    Overlap-based fidelity proxy: sum of min(ideal_prob, noisy_prob) per
    bitstring, a standard classical distributional-overlap measure,
    compared against the analytical estimated_fidelity. A real,
    substantial disagreement between two independently-computed numbers
    is a genuine signal something's off — either the noise model, the
    calibration data behind it, or the analytical approximation's
    assumptions don't hold for this specific circuit.

    Informational only (does not BLOCK) — this is the newest, least
    battle-tested check in the pipeline; flagging a real disagreement is
    more useful right now than silently deciding it's always right.
    """
    if hw_result.get("counts") is None or hw_result.get("estimated_fidelity") is None:
        return {"applicable": False, "note": "Needs both a full noisy simulation and an "
                                              "analytical estimate — at least one is missing "
                                              "(see hardware_aware_simulation's own error/note)."}

    ideal = ideal_simulation(circuit, shots)
    ideal_total = ideal["total_shots"]
    ideal_probs = {b: c / ideal_total for b, c in ideal["counts"].items()} if ideal_total else {}

    noisy_counts = hw_result["counts"]
    noisy_total = sum(noisy_counts.values())
    noisy_probs = {b: c / noisy_total for b, c in noisy_counts.items()} if noisy_total else {}

    overlap = sum(min(ideal_probs.get(b, 0.0), noisy_probs.get(b, 0.0))
                  for b in set(ideal_probs) | set(noisy_probs))

    analytical = hw_result["estimated_fidelity"]
    disagreement = abs(overlap - analytical)
    significant = disagreement > 0.25  # both are 0-1 fidelity-like quantities

    return {
        "applicable": True,
        "analytical_estimate": analytical,
        "simulated_overlap_fidelity": round(overlap, 4),
        "disagreement": round(disagreement, 4),
        "significant_disagreement": significant,
        "verdict": (
            f"Analytical estimate ({analytical}) and the real noisy simulation's overlap "
            f"({round(overlap, 4)}) disagree by {round(disagreement, 4)} — worth investigating "
            "before trusting either number for this circuit."
            if significant else
            f"Analytical estimate ({analytical}) and the real noisy simulation's overlap "
            f"({round(overlap, 4)}) are consistent — two independently-computed methods agree."
        ),
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


def required_shots_check(n_marked: int, expected_amplification: float, n_qubits: int,
                          requested_shots: int, confidence: float = 0.95, power: float = 0.80) -> dict:
    """
    Real, concrete answer to "could this claim ever be distinguishable from
    noise at the shots I'm about to request" — computed BEFORE any real
    hardware time is spent, not discovered after an inconclusive result
    already cost money. Added 2026-08-24.

    Standard two-proportion sample-size formula: null hypothesis is the
    uniform/no-real-effect probability of landing on a marked bitstring
    (p0 = n_marked / 2**n_qubits); the claim is that entanglement/the
    circuit's real structure pushes that to p1 = expected_amplification * p0.
    Computes the minimum shots needed to separate p0 from p1 at the given
    confidence (Type I error) and power (Type II error, 1 - false-negative
    rate), then compares against what was actually requested.
    """
    from scipy.stats import norm

    p0 = n_marked / (2 ** n_qubits)
    p1 = min(expected_amplification * p0, 1.0)

    if p1 <= p0:
        return {
            "applicable": False,
            "note": f"expected_amplification={expected_amplification}x implies no real effect "
                    f"over the {round(p0, 6)} baseline — a power check doesn't apply the same way "
                    "to a claim of 'no improvement'.",
        }

    z_alpha = norm.ppf(1 - (1 - confidence) / 2)
    z_beta = norm.ppf(power)
    pbar = (p0 + p1) / 2
    numerator = (z_alpha * math.sqrt(2 * pbar * (1 - pbar))
                 + z_beta * math.sqrt(p0 * (1 - p0) + p1 * (1 - p1)))
    required_shots = math.ceil((numerator / (p1 - p0)) ** 2)
    passed = requested_shots >= required_shots

    return {
        "applicable": True,
        "null_hypothesis_probability": round(p0, 6),
        "claimed_probability": round(p1, 6),
        "required_shots": required_shots,
        "requested_shots": requested_shots,
        "confidence": confidence,
        "power": power,
        "passed": passed,
        "verdict": (
            f"{requested_shots} shots is enough to distinguish this claim from noise "
            f"({required_shots} required at {int(confidence*100)}% confidence, {int(power*100)}% power)."
            if passed else
            f"{requested_shots} shots is NOT enough — this claim cannot be reliably distinguished "
            f"from noise even if it's completely true. Needs at least {required_shots} shots at "
            f"{int(confidence*100)}% confidence, {int(power*100)}% power. Running this experiment "
            "as requested risks an inconclusive result regardless of what the hardware actually did."
        ),
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

    if expected_marked_bitstrings and expected_amplification is not None:
        power_check = required_shots_check(
            len(expected_marked_bitstrings), expected_amplification, circuit.num_qubits, shots,
        )
        result["required_shots_check"] = power_check
        if power_check["applicable"] and not power_check["passed"]:
            return {**result, "verdict": "BLOCK", "reason": power_check["verdict"]}

    ideal = ideal_simulation(circuit, shots)
    result["ideal_simulation"] = {"total_shots": ideal["total_shots"],
                                   "top_outcomes": dict(sorted(ideal["counts"].items(), key=lambda x: -x[1])[:5])}

    hw = hardware_aware_simulation(circuit, provider, target_device, shots)
    result["hardware_aware_simulation"] = hw
    if "error" in hw:
        return {**result, "verdict": "BLOCK", "reason": hw["error"]}

    gsc = hw.get("gate_synthesis_check")
    if gsc and not gsc["passed"]:
        return {**result, "verdict": "BLOCK",
                "reason": "Gate synthesis check failed — the circuit doesn't map cleanly onto "
                           "the target's native gateset, likely a missing gate equivalence rather "
                           "than real hardware overhead.",
                "details": gsc}

    if provider == "ibm":
        result["fidelity_cross_check"] = cross_check_fidelity_estimate(circuit, hw, shots)

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
