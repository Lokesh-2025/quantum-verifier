"""
IonQ provider — device listing, batched job submission with a self-check
gate, job status/results, and pre-submission gate/cost estimation.

Copied from quantum-hardware-mcp/server.py (untouched original) and adapted
to be a plain, importable module — no MCP dependency, no @mcp.tool()
decorators, and returning native Python dicts instead of JSON strings
(the MCP wrapper layer handles serialization).

ionq_submit_job's self-check pattern (transpile against the real target
device's native gateset, simulate with the matching noise model, refuse on
mismatch — one bad circuit refuses the WHOLE batch) is the closest existing
precedent to this project's core Verifier and is preserved verbatim.
"""
import os
import requests

_IONQ_BACKEND_ALIASES = {
    "simulator":              "ionq_simulator",
    "ionq_simulator":         "ionq_simulator",
    "forte-1":                "qpu.forte-1",
    "forte1":                 "qpu.forte-1",
    "qpu.forte-1":            "qpu.forte-1",
    "forte-enterprise-1":     "qpu.forte-enterprise-1",
    "forte-enterprise":       "qpu.forte-enterprise-1",
    "qpu.forte-enterprise-1": "qpu.forte-enterprise-1",
}


def _resolve_ionq_backend(name: str) -> str:
    """Map a friendly IonQ backend name to the exact string qiskit_ionq needs."""
    key = name.strip().lower()
    return _IONQ_BACKEND_ALIASES.get(key, name)


def _ionq_is_hardware(resolved_backend_name: str) -> bool:
    """True if this backend name refers to real QPU hardware, not a simulator."""
    return "simulator" not in resolved_backend_name.lower()


def ionq_devices() -> dict | list:
    """All IonQ backends and simulators with live status. Requires IONQ_API_KEY."""
    api_key = os.getenv("IONQ_API_KEY")
    if not api_key:
        return {"error": "IONQ_API_KEY not set in .env",
                "hint": "Get your key at cloud.ionq.com and add IONQ_API_KEY=your_key to .env"}
    try:
        resp = requests.get(
            "https://api.ionq.co/v0.3/backends",
            headers={"Authorization": f"apiKey {api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        backends = resp.json()
        result = []
        for b in backends:
            name = b.get("backend", b.get("name", "unknown"))
            status = b.get("status")
            result.append({
                "name": name, "num_qubits": b.get("qubits"),
                "available": status == "available", "status": status,
                "type": "simulator" if "simulator" in name else "hardware",
                "provider": "IonQ", "technology": "trapped-ion",
            })
        return result
    except Exception as e:
        return {"error": str(e)}


def ionq_submit_job(
    backend_name: str,
    qasm_circuits: list,
    shots: int = 1024,
    optimization_level: int = 1,
    expected_marked_bitstrings=None,
    expected_amplification=None,
    amplification_tolerance: float = 0.5,
    confirm_real_hardware: bool = False,
) -> dict:
    """
    Compile and submit one or more OpenQASM 2 circuits to IonQ as ONE job.

    Every circuit is transpiled against the real target device's native
    gateset and simulated first (with the matching noise model if targeting
    real hardware). If expected_amplification is given and misses by more
    than amplification_tolerance, the WHOLE batch is refused before
    anything touches real hardware. Real hardware additionally requires
    confirm_real_hardware=True.
    """
    api_key = os.getenv("IONQ_API_KEY")
    if not api_key:
        return {"error": "IONQ_API_KEY not set in .env"}

    if isinstance(qasm_circuits, str):
        qasm_circuits = [qasm_circuits]
    if not qasm_circuits:
        return {"error": "qasm_circuits is empty"}

    try:
        from qiskit_ionq import IonQProvider
        from qiskit import QuantumCircuit as QC, transpile

        circuits = []
        for i, qasm_string in enumerate(qasm_circuits):
            try:
                circuits.append(QC.from_qasm_str(qasm_string))
            except Exception as parse_err:
                return {"error": f"Failed to parse circuit {i}: {parse_err}"}

        resolved_backend = _resolve_ionq_backend(backend_name)
        is_hardware = _ionq_is_hardware(resolved_backend)
        n_circuits = len(circuits)

        def _per_circuit(value, param_name):
            if value is None:
                return [None] * n_circuits
            if n_circuits == 1:
                return [value]
            if not isinstance(value, list) or len(value) != n_circuits:
                raise ValueError(
                    f"{param_name}: submitting {n_circuits} circuits requires a list of "
                    f"{n_circuits} entries (one per circuit, None to skip that circuit's check)"
                )
            return value

        try:
            marked_per_circuit = _per_circuit(expected_marked_bitstrings, "expected_marked_bitstrings")
            expected_amp_per_circuit = _per_circuit(expected_amplification, "expected_amplification")
        except ValueError as ve:
            return {"error": str(ve)}

        provider = IonQProvider(api_key)
        target_backend = provider.get_backend(resolved_backend, gateset="native")
        sim_backend = provider.get_backend("ionq_simulator", gateset="native")
        if is_hardware:
            device_short_name = resolved_backend.replace("qpu.", "")
            sim_backend.set_options(noise_model=device_short_name)

        self_check = {"ran": True, "per_circuit": [], "passed": True,
                       "noise_model_used": sim_backend.options.noise_model}
        for i, qc in enumerate(circuits):
            t_qc = transpile(qc, backend=target_backend, optimization_level=optimization_level)
            sim_job = sim_backend.run(t_qc, shots=shots)
            sim_counts = sim_job.result().get_counts()
            total = sum(sim_counts.values())

            entry = {
                "circuit_index": i, "num_qubits": qc.num_qubits,
                "transpiled_gate_count": t_qc.size(),
                "simulated_counts_top5": dict(sorted(sim_counts.items(), key=lambda x: -x[1])[:5]),
            }

            circuit_marked = marked_per_circuit[i]
            circuit_expected_amp = expected_amp_per_circuit[i]
            if circuit_marked:
                marked = set(circuit_marked)
                marked_shots = sum(c for b, c in sim_counts.items() if b in marked)
                sim_amp = (marked_shots / total) / (len(marked) / (2 ** qc.num_qubits)) if total else 0
                entry["simulated_amplification"] = round(sim_amp, 3)
                if circuit_expected_amp is not None:
                    lo = circuit_expected_amp * (1 - amplification_tolerance)
                    hi = circuit_expected_amp * (1 + amplification_tolerance)
                    entry["expected_amplification"] = circuit_expected_amp
                    entry["within_tolerance"] = lo <= sim_amp <= hi
                    if not entry["within_tolerance"]:
                        self_check["passed"] = False
            self_check["per_circuit"].append(entry)

        if not self_check["passed"]:
            failed_indices = [e["circuit_index"] for e in self_check["per_circuit"]
                               if e.get("within_tolerance") is False]
            return {
                "error": f"Self-check failed on circuit(s) {failed_indices}",
                "hint": "One bad circuit refuses the WHOLE batch — none of them submitted.",
                "self_check": self_check,
            }

        if resolved_backend == "ionq_simulator":
            return {
                "status": "SIMULATED", "backend": resolved_backend, "is_real_hardware": False,
                "shots": shots, "provider": "IonQ", "self_check": self_check,
                "note": "backend was 'simulator' — nothing was billed.",
            }

        if is_hardware and not confirm_real_hardware:
            return {
                "error": f"'{resolved_backend}' is real QPU hardware and will be billed.",
                "hint": "Pass confirm_real_hardware=True to actually submit.",
                "self_check": self_check,
            }

        t_circuits = [transpile(qc, backend=target_backend, optimization_level=optimization_level) for qc in circuits]
        job = target_backend.run(t_circuits, shots=shots)
        return {
            "job_id": job.job_id(), "status": "SUBMITTED", "backend": resolved_backend,
            "is_real_hardware": True, "num_circuits": len(circuits), "shots": shots,
            "provider": "IonQ", "self_check": self_check,
        }
    except Exception as e:
        return {"error": str(e)}


def ionq_job_status(job_id: str, backend_name: str = "ionq_simulator") -> dict:
    """Status of a submitted IonQ job."""
    api_key = os.getenv("IONQ_API_KEY")
    if not api_key:
        return {"error": "IONQ_API_KEY not set in .env"}
    try:
        from qiskit_ionq import IonQProvider
        resolved_backend = _resolve_ionq_backend(backend_name)
        provider = IonQProvider(api_key)
        backend = provider.get_backend(resolved_backend, gateset="native")
        job = backend.retrieve_job(job_id)
        status = job.status()
        return {
            "job_id": job_id, "status": str(status.name), "backend": resolved_backend,
            "is_real_hardware": _ionq_is_hardware(resolved_backend), "provider": "IonQ",
        }
    except Exception as e:
        return {"error": str(e)}


def ionq_job_results(job_id: str, backend_name: str = "simulator") -> dict:
    """Measurement counts from a completed IonQ job. is_real_hardware set from the backend name itself, never guessed."""
    api_key = os.getenv("IONQ_API_KEY")
    if not api_key:
        return {"error": "IONQ_API_KEY not set in .env"}
    try:
        from qiskit_ionq import IonQProvider
        from qiskit.providers import JobStatus
        resolved_backend = _resolve_ionq_backend(backend_name)
        provider = IonQProvider(api_key)
        backend = provider.get_backend(resolved_backend, gateset="native")
        job = backend.retrieve_job(job_id)
        status = job.status()
        if status != JobStatus.DONE:
            return {"job_id": job_id, "status": str(status.name), "message": "Job not complete yet"}
        result = job.result()
        counts = result.get_counts()
        is_batch = isinstance(counts, list)
        payload = {
            "job_id": job_id, "backend": resolved_backend,
            "is_real_hardware": _ionq_is_hardware(resolved_backend), "provider": "IonQ",
        }
        if is_batch:
            payload["counts"] = counts
            payload["total_shots_per_circuit"] = [sum(c.values()) for c in counts]
        else:
            payload["counts"] = counts
            payload["total_shots"] = sum(counts.values())
        return payload
    except Exception as e:
        return {"error": str(e)}


def estimate_ionq_gates(qasm_string: str, backend_name: str = "forte-1", optimization_level: int = 1) -> dict:
    """Native gate count (GPI/GPI2/ZZ) for a circuit, transpiled against a real device's actual native target."""
    try:
        from qiskit import QuantumCircuit as QC, transpile
        try:
            circuit = QC.from_qasm_str(qasm_string)
        except Exception as parse_err:
            return {"error": f"Failed to parse QASM: {parse_err}"}
        api_key = os.getenv("IONQ_API_KEY")
        if not api_key:
            return {"error": "IONQ_API_KEY not set in .env"}
        from qiskit_ionq import IonQProvider
        resolved_backend = _resolve_ionq_backend(backend_name)
        backend = IonQProvider(api_key).get_backend(resolved_backend, gateset="native")
        t_circuit = transpile(circuit, backend=backend, optimization_level=optimization_level)
        ops = dict(t_circuit.count_ops())
        two_qubit_gates = ops.get("zz", 0) + ops.get("ms", 0)
        one_qubit_gates = ops.get("gpi", 0) + ops.get("gpi2", 0)
        native_2q_gate = "zz" if "zz" in ops else ("ms" if "ms" in ops else None)
        est_2q_seconds = two_qubit_gates * 0.00015
        return {
            "target_device": resolved_backend, "native_2q_gate_family": native_2q_gate,
            "num_qubits_in_circuit": circuit.num_qubits, "native_gate_counts": ops,
            "one_qubit_gates": one_qubit_gates, "two_qubit_gates": two_qubit_gates,
            "total_native_gates": sum(ops.values()),
            "estimated_seconds_per_shot": round(est_2q_seconds, 4),
        }
    except Exception as e:
        return {"error": str(e)}


def estimate_ionq_cost(qasm_circuits: list, shots: int = 4096) -> dict:
    """Dollar cost preview using IonQ's real per-job pricing floor, verified against IonQ's own resource estimator."""
    JOB_FLOOR_USD = 168.20
    KNOWN_ABOVE_FLOOR_POINT_2Q_GATES = 600
    KNOWN_ABOVE_FLOOR_POINT_USD = 3294.87
    ROUGH_USD_PER_2Q_GATE = (KNOWN_ABOVE_FLOOR_POINT_USD - JOB_FLOOR_USD) / KNOWN_ABOVE_FLOOR_POINT_2Q_GATES

    if isinstance(qasm_circuits, str):
        qasm_circuits = [qasm_circuits]
    api_key = os.getenv("IONQ_API_KEY")
    if not api_key:
        return {"error": "IONQ_API_KEY not set in .env"}
    try:
        from qiskit import QuantumCircuit as QC, transpile
        from qiskit_ionq import IonQProvider
        backend = IonQProvider(api_key).get_backend("qpu.forte-1", gateset="native")
        per_circuit = []
        max_two_qubit_gates = 0
        for i, qasm_string in enumerate(qasm_circuits):
            circuit = QC.from_qasm_str(qasm_string)
            t_circuit = transpile(circuit, backend=backend, optimization_level=1)
            ops = dict(t_circuit.count_ops())
            two_q = ops.get("zz", 0)
            max_two_qubit_gates = max(max_two_qubit_gates, two_q)
            per_circuit.append({
                "circuit_index": i, "num_qubits": circuit.num_qubits,
                "two_qubit_gates": two_q, "one_qubit_gates": ops.get("gpi", 0) + ops.get("gpi2", 0),
            })
        likely_at_floor = max_two_qubit_gates <= 20
        if likely_at_floor:
            estimated_total = JOB_FLOOR_USD
            confidence = "high — verified empirically for circuits in this size range"
        else:
            estimated_total = JOB_FLOOR_USD + ROUGH_USD_PER_2Q_GATE * max_two_qubit_gates
            confidence = "LOW — extrapolated from a single data point"
        return {
            "num_circuits_in_batch": len(qasm_circuits), "shots_per_circuit": shots,
            "per_circuit": per_circuit, "job_floor_usd": JOB_FLOOR_USD,
            "likely_at_floor": likely_at_floor,
            "estimated_total_usd": round(estimated_total, 2), "confidence": confidence,
        }
    except Exception as e:
        return {"error": str(e)}
