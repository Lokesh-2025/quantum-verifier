"""
Control-experiment generator — the flagship capability from this project's
design consultations.

Given a circuit that claims some effect (e.g. "this shows amplification"),
automatically builds a SECOND circuit — a control — that removes the
entangling mechanism the claim depends on, but keeps everything else
(qubit count, single-qubit gates, measurement structure) identical. Both
circuits run through the same hardware-aware simulation path. The
difference between them is the real, isolated effect size — confounds like
readout bias or SPAM error show up in BOTH circuits equally and cancel out
of the comparison.

This is the general case, deliberately preferred over known-answer
checking in verifier.py's ground_truth_check: it works even in genuine
discovery-mode research, where there is no known correct answer to compare
against — exactly the situation equality_oracle_search was built for.
"""
from qiskit import QuantumCircuit

from core.verifier import hardware_aware_simulation

# Two-qubit entangling gate names to strip when building the control.
# This covers every entangling gate this project's circuits actually use
# across both vendors — IBM's rzz/cx/ecr/cz and IonQ's native zz/ms.
ENTANGLING_GATE_NAMES = {"rzz", "cx", "cz", "ecr", "zz", "ms", "cnot", "swap"}


def build_control_circuit(circuit: QuantumCircuit) -> QuantumCircuit:
    """
    Return a new circuit identical to the input EXCEPT every entangling
    (two-qubit) gate is removed. Single-qubit gates, qubit/clbit counts,
    and the measurement structure are all preserved exactly, so both
    circuits are directly comparable — the only thing that changed is
    whether the claimed entangling mechanism is present.
    """
    control = QuantumCircuit(circuit.num_qubits, circuit.num_clbits)
    for instruction in circuit.data:
        name = instruction.operation.name.lower()
        if name in ENTANGLING_GATE_NAMES:
            continue  # strip the claimed mechanism
        qubit_indices = [circuit.find_bit(q).index for q in instruction.qubits]
        clbit_indices = [circuit.find_bit(c).index for c in instruction.clbits]
        control.append(instruction.operation, qubit_indices, clbit_indices)
    return control


def falsify(
    qasm_string: str,
    provider: str,
    target_device: str,
    marked_bitstrings: list = None,
    shots: int = 4096,
) -> dict:
    """
    Run the circuit AND its auto-generated control through the same
    hardware-aware simulation, and report the isolated effect size.

    If marked_bitstrings is given, reports the difference in "signal"
    (fraction of shots landing on those states) between the real circuit
    and the control — this is the real, confound-isolated effect size.

    If marked_bitstrings is NOT given (true discovery mode — no known
    target), reports which bitstrings gained the most probability going
    from control to full circuit, using total variation distance as the
    overall signal-strength summary. This still works without knowing the
    "right answer" in advance, which is the whole point.
    """
    try:
        circuit = QuantumCircuit.from_qasm_str(qasm_string)
    except Exception as e:
        return {"error": f"Failed to parse QASM: {e}"}

    control_circuit = build_control_circuit(circuit)
    n_entangling_removed = sum(
        1 for instr in circuit.data if instr.operation.name.lower() in ENTANGLING_GATE_NAMES
    )
    if n_entangling_removed == 0:
        return {
            "error": "No entangling gates found to remove — this circuit makes no "
                     "entanglement-dependent claim to falsify. The control would be "
                     "identical to the original."
        }

    real_sim = hardware_aware_simulation(circuit, provider, target_device, shots)
    if "error" in real_sim:
        return {"error": f"Real circuit simulation failed: {real_sim['error']}"}

    control_sim = hardware_aware_simulation(control_circuit, provider, target_device, shots)
    if "error" in control_sim:
        return {"error": f"Control circuit simulation failed: {control_sim['error']}"}

    result = {
        "entangling_gates_removed": n_entangling_removed,
        "real_circuit": {"counts": real_sim.get("counts"), "simulation_type": real_sim.get("simulation_type")},
        "control_circuit": {"counts": control_sim.get("counts"), "simulation_type": control_sim.get("simulation_type")},
    }

    real_counts = real_sim.get("counts")
    control_counts = control_sim.get("counts")
    if not real_counts or not control_counts:
        result["note"] = ("IBM's hardware-aware path returns a fidelity estimate, not raw "
                           "counts — the control-experiment comparison needs actual counts, "
                           "so this only works on the IonQ path today.")
        return result

    if marked_bitstrings:
        real_total = sum(real_counts.values())
        control_total = sum(control_counts.values())
        marked = set(marked_bitstrings)
        real_signal = sum(c for b, c in real_counts.items() if b in marked) / real_total if real_total else 0
        control_signal = sum(c for b, c in control_counts.items() if b in marked) / control_total if control_total else 0
        isolated_effect = real_signal - control_signal
        result["marked_bitstrings"] = marked_bitstrings
        result["real_circuit_signal"] = round(real_signal, 4)
        result["control_circuit_signal"] = round(control_signal, 4)
        result["isolated_effect_size"] = round(isolated_effect, 4)
        result["interpretation"] = (
            f"The claimed effect measures {round(real_signal, 3)} in the real circuit and "
            f"{round(control_signal, 3)} in the control (same circuit, entanglement removed). "
            f"The isolated, confound-free effect size is {round(isolated_effect, 3)} — "
            "this is what the entangling mechanism itself contributes, with SPAM/readout "
            "bias (which affects both circuits equally) subtracted out."
        )
    else:
        all_keys = set(real_counts) | set(control_counts)
        real_total = sum(real_counts.values())
        control_total = sum(control_counts.values())
        tvd = 0.5 * sum(
            abs(real_counts.get(k, 0) / real_total - control_counts.get(k, 0) / control_total)
            for k in all_keys
        )
        gains = sorted(
            all_keys,
            key=lambda k: (real_counts.get(k, 0) / real_total) - (control_counts.get(k, 0) / control_total),
            reverse=True,
        )[:5]
        result["total_variation_distance"] = round(tvd, 4)
        result["bitstrings_most_boosted_by_entanglement"] = gains
        result["interpretation"] = (
            f"No target bitstrings were specified (discovery mode). The real circuit's "
            f"output distribution differs from its entanglement-free control by a total "
            f"variation distance of {round(tvd, 3)} (0 = identical, 1 = completely different). "
            f"The bitstrings most boosted by adding entanglement are listed above — these "
            "are candidate answers the entangling mechanism is actually responsible for, "
            "as distinct from anything the single-qubit structure alone would produce."
        )

    return result
