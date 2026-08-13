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
import math
import os
import requests

from qiskit.circuit import Parameter
from qiskit.circuit.library import RZZGate
from qiskit.circuit.equivalence_library import SessionEquivalenceLibrary as _sel

# Same fix as core/verifier.py's _register_ionq_native_equivalences, duplicated
# here (not imported) so this module is correct on its own regardless of what
# else got imported first -- the exact class of mistake that let an
# unfixed submission go out: RZZ(theta) [radians] == native ZZ(theta/(2*pi))
# [turns] exactly (verified via Operator comparison), but qiskit's transpiler
# has no built-in equivalence between them and silently falls back to
# expensive, inflated general two-qubit synthesis without this registered.
def _register_ionq_native_equivalences():
    try:
        from qiskit_ionq.ionq_gates import ZZGate as _IonQZZGate
    except ImportError:
        return
    theta = Parameter("theta")
    equiv = __import__("qiskit").QuantumCircuit(2)
    equiv.append(_IonQZZGate(theta / (2 * math.pi)), [0, 1])
    _sel.add_equivalence(RZZGate(theta), equiv)


_register_ionq_native_equivalences()

# IonQ's native ZZ gate is only valid for |theta_turns| <= 0.25 (a quarter
# turn) -- confirmed via a real IonQ API rejection when a large-angle rzz hit
# the 1:1 equivalence above. Splitting into N chained smaller RZZ
# applications is mathematically EXACT: ZZ generators on the same qubit pair
# commute, so N reps of RZZ(theta/N) = RZZ(theta) exactly. Duplicated here
# (not imported from core.verifier) so this module is correct standalone.
IONQ_NATIVE_ZZ_MAX_TURNS = 0.25


def _decompose_large_angle_rzz(circuit):
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


_JOB_FLOOR_USD = 168.20
_KNOWN_ABOVE_FLOOR_POINT_2Q_GATES = 600
_KNOWN_ABOVE_FLOOR_POINT_USD = 3294.87
# Deliberately the HIGHER of the two real above-floor rates now known (see
# estimate_ionq_cost's docstring -- a real batched job came in at a rate
# 2.65x lower than this one). Budget-refusal safety logic should stay
# conservative and over-estimate rather than under-estimate; the honest
# wide range belongs in estimate_ionq_cost's own output, not silently
# baked into whether a submission gets blocked.
_ROUGH_USD_PER_2Q_GATE = (_KNOWN_ABOVE_FLOOR_POINT_USD - _JOB_FLOOR_USD) / _KNOWN_ABOVE_FLOOR_POINT_2Q_GATES


def _estimate_cost_from_circuits(decomposed_circuits: list) -> float:
    """Same (deliberately conservative/high) cost model used for the budget
    preflight check, applied to already-decomposed circuits (so it reflects
    the real gate count that will actually run)."""
    max_two_qubit_gates = max(
        (sum(1 for instr in qc.data if instr.operation.name == "rzz") for qc in decomposed_circuits),
        default=0,
    )
    if max_two_qubit_gates <= 20:
        return _JOB_FLOOR_USD
    return _JOB_FLOOR_USD + _ROUGH_USD_PER_2Q_GATE * max_two_qubit_gates


def _check_budget_before_submitting(resolved_backend: str, decomposed_circuits: list) -> dict:
    """
    Refuses submission with a clear reason if the estimated cost exceeds
    the actual remaining budget of the project this backend belongs to --
    built specifically because this project twice hit a real, opaque
    QuotaExhaustedError from IonQ's API after already passing every other
    check, once from an unnoticed $0 project budget and once from an
    under-estimated real cost. Catches both classes here instead.
    """
    estimated_cost = _estimate_cost_from_circuits(decomposed_circuits)
    account = ionq_account_check()
    if account.get("error"):
        return {"error": None}  # can't check -- don't block submission on this failing, just skip the check
    matching = [p for p in account["projects"] if resolved_backend in (p.get("allowed_targets") or [])]
    if not matching:
        return {"error": None}  # no project info to check against -- let IonQ's own API be the final word
    project = matching[0]
    remaining = project["budget_remaining_usd"]
    if estimated_cost > remaining:
        return {
            "error": f"Estimated cost (~${estimated_cost:.2f}, rough) exceeds remaining budget "
                     f"(${remaining:.2f}) on project '{project['name']}'.",
            "hint": "Raise the project's budget in the IonQ dashboard, or reduce circuit "
                    "complexity (large-angle rzz gates get split into multiple native gates, "
                    "which is often the real cost driver), then resubmit.",
            "estimated_cost_usd": round(estimated_cost, 2),
            "budget_remaining_usd": remaining,
            "project": project["name"],
        }
    return {"error": None}


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
                parsed = QC.from_qasm_str(qasm_string)
            except Exception as parse_err:
                return {"error": f"Failed to parse circuit {i}: {parse_err}"}
            circuits.append(_decompose_large_angle_rzz(parsed))

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
                if is_hardware:
                    from qiskit import qasm2
                    from core.memory import record_prediction
                    pred = record_prediction(
                        qasm2.dumps(qc), provider="ionq", target_device=resolved_backend,
                        predicted_amplification=sim_amp, marked_bitstrings=circuit_marked,
                        source="ionq_submit_job_self_check", circuit_index_in_job=i,
                    )
                    entry["memory_prediction_id"] = pred.get("prediction_id")
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

        if is_hardware:
            budget_check = _check_budget_before_submitting(resolved_backend, circuits)
            if budget_check.get("error"):
                return {**budget_check, "self_check": self_check}

        t_circuits = [transpile(qc, backend=target_backend, optimization_level=optimization_level) for qc in circuits]
        job = target_backend.run(t_circuits, shots=shots)
        job_id = job.job_id()

        from core.memory import attach_job_id
        for entry in self_check["per_circuit"]:
            pred_id = entry.get("memory_prediction_id")
            if pred_id:
                attach_job_id(pred_id, job_id)

        return {
            "job_id": job_id, "status": "SUBMITTED", "backend": resolved_backend,
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


def ionq_sync_memory_for_job(job_id: str) -> dict:
    """
    Completes Experiment Memory for a real job: fetches its actual results
    directly via IonQ's REST API (not the SDK's job.result().get_counts()
    path, which has a known bug on batched real-hardware jobs — it tries
    to parse a child job's UUID as an integer and throws), computes the
    real observed amplification for each circuit using the marked
    bitstrings recorded at submission time, and records it against the
    matching prediction.

    Call this once a job submitted through ionq_submit_job has actually
    completed — nothing calls this automatically yet, since polling for
    job completion is a separate concern from submission.
    """
    api_key = os.getenv("IONQ_API_KEY")
    if not api_key:
        return {"error": "IONQ_API_KEY not set in .env"}
    try:
        from core.memory import find_predictions_for_job, record_real_result

        predictions = find_predictions_for_job(job_id)
        if not predictions:
            return {"error": f"No predictions in memory are linked to job {job_id}."}

        headers = {"Authorization": f"apiKey {api_key}"}
        resp = requests.get(f"https://api.ionq.co/v0.3/jobs/{job_id}", headers=headers, timeout=15)
        resp.raise_for_status()
        job_data = resp.json()
        children = job_data.get("children") or [job_id]  # single-circuit jobs have no children

        results = []
        for pred in predictions:
            idx = pred["circuit_index_in_job"] or 0
            if idx >= len(children):
                results.append({"prediction_id": pred["prediction_id"], "error": "circuit index out of range for this job's children"})
                continue
            child_id = children[idx]
            hist_resp = requests.get(
                f"https://api.ionq.co/v0.4/jobs/{child_id}/results/histogram", headers=headers, timeout=15
            )
            if hist_resp.status_code != 200:
                results.append({"prediction_id": pred["prediction_id"], "error": f"could not fetch results for child {child_id} (status {hist_resp.status_code}, job may not be complete)"})
                continue
            hist = {int(k): v for k, v in hist_resp.json().items()}
            total = sum(hist.values())
            marked_bitstrings = pred["marked_bitstrings"] or []
            marked_decimal = {int(b, 2) for b in marked_bitstrings}
            n_qubits = len(marked_bitstrings[0]) if marked_bitstrings else None
            marked_shots = sum(v for k, v in hist.items() if k in marked_decimal)
            baseline = len(marked_decimal) / (2 ** n_qubits) if n_qubits and total else None
            real_amp = (marked_shots / total) / baseline if baseline else None

            record_real_result(pred["prediction_id"], real_amp, real_job_id=job_id)
            results.append({
                "prediction_id": pred["prediction_id"], "circuit_index": idx,
                "predicted_amplification": pred["predicted_amplification"],
                "real_amplification": round(real_amp, 3) if real_amp is not None else None,
            })
        return {"job_id": job_id, "synced": results}
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
        circuit = _decompose_large_angle_rzz(circuit)
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
    """
    Dollar cost preview using IonQ's real per-job pricing floor.

    Above the floor, this reports a RANGE, not a single number, and that's
    a deliberate honesty choice, not a hedge: two real data points exist
    for above-floor pricing now (an original single-circuit point at 600
    two-qubit gates -> $3294.87, and a real batched job this project ran
    at 36 max two-qubit gates -> $238.96 actually charged), and they imply
    per-gate rates 2.65x apart ($5.21 vs $1.97). That's real evidence a
    simple "cost scales linearly with the hardest single circuit's gate
    count" model isn't well-supported — it may actually depend on total
    gates across the whole batch, not just the worst circuit, or scale
    non-linearly, and there isn't yet enough real data to tell which.
    Reporting one fake-precise number here would be less honest than this
    range, even though the range is wide.
    """
    JOB_FLOOR_USD = 168.20
    HIGH_RATE_USD_PER_2Q_GATE = (3294.87 - JOB_FLOOR_USD) / 600       # original single-circuit point
    LOW_RATE_USD_PER_2Q_GATE = (238.956544 - JOB_FLOOR_USD) / 36      # real batched job, 2026-08-13

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
            circuit = _decompose_large_angle_rzz(QC.from_qasm_str(qasm_string))
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
            low = high = JOB_FLOOR_USD
            confidence = "high — verified empirically for circuits in this size range"
        else:
            low = JOB_FLOOR_USD + LOW_RATE_USD_PER_2Q_GATE * max_two_qubit_gates
            high = JOB_FLOOR_USD + HIGH_RATE_USD_PER_2Q_GATE * max_two_qubit_gates
            confidence = ("LOW — two real data points disagree on the per-gate rate by 2.65x; "
                          "this range spans both. Budget for the HIGH end, verify on IonQ's "
                          "real calculator before relying on this for anything precise.")
        return {
            "num_circuits_in_batch": len(qasm_circuits), "shots_per_circuit": shots,
            "per_circuit": per_circuit, "job_floor_usd": JOB_FLOOR_USD,
            "likely_at_floor": likely_at_floor,
            "estimated_total_usd_low": round(low, 2), "estimated_total_usd_high": round(high, 2),
            "confidence": confidence,
            "note": "This is ONE job (batched) — all circuits above share this one floor, not pay it individually.",
        }
    except Exception as e:
        return {"error": str(e)}


def ionq_account_check() -> dict:
    """
    Surfaces which IonQ project(s)/organization this API key can actually
    submit to, and their real budget status -- built specifically because
    this project once spent a long, unknown stretch of time pointed at the
    wrong (unfunded) IonQ organization, with the real, funded one sitting
    untouched and never even having an API key generated for it. Nothing
    in the submission path would have caught that on its own; this makes
    it visible up front instead of requiring a manual dashboard dig.

    Flags any accessible project with zero remaining budget explicitly --
    that's exactly the shape of the mixup that motivated this tool.
    """
    api_key = os.getenv("IONQ_API_KEY")
    if not api_key:
        return {"error": "IONQ_API_KEY not set in .env"}
    try:
        resp = requests.get(
            "https://api.ionq.co/v0.3/projects",
            headers={"Authorization": f"apiKey {api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        projects = resp.json().get("projects", [])
        result = []
        for p in projects:
            limit = p.get("quotaLimit", 0)
            usage = p.get("quotaUsage", 0)
            remaining = limit - usage
            result.append({
                "project_id": p.get("id"), "name": p.get("name"),
                "budget_limit_usd": limit, "budget_used_usd": round(usage, 2),
                "budget_remaining_usd": round(remaining, 2),
                "allowed_targets": p.get("allowedTargets"),
                "last_job_run_time": p.get("lastJobRunTime"),
                "zero_budget_warning": limit == 0,
            })
        zero_budget = [p["name"] for p in result if p["zero_budget_warning"]]
        return {
            "projects": result,
            "note": (f"WARNING: {len(zero_budget)} accessible project(s) have $0 budget "
                     f"configured: {zero_budget} -- if you expected to be submitting real "
                     "jobs against one of these, check you're using the right API key/project."
                     if zero_budget else
                     "All accessible projects have a nonzero budget configured."),
        }
    except Exception as e:
        return {"error": str(e)}


def ionq_compare_devices() -> dict:
    """
    Ranks IonQ's real hardware devices by actual live calibration data --
    2-qubit gate fidelity, coherence time, gate speed, readout fidelity --
    instead of picking one out of habit or because it was mentioned first
    in an old doc. Mirrors the IBM-side compare_devices tool, which IonQ
    never had an equivalent for.
    """
    api_key = os.getenv("IONQ_API_KEY")
    if not api_key:
        return {"error": "IONQ_API_KEY not set in .env"}
    try:
        headers = {"Authorization": f"apiKey {api_key}"}
        resp = requests.get("https://api.ionq.co/v0.3/backends", headers=headers, timeout=15)
        resp.raise_for_status()
        backends = resp.json()
        devices = []
        for b in backends:
            name = b.get("backend", "")
            if "simulator" in name.lower() or b.get("status") != "available":
                continue
            char_url = b.get("characterization_url")
            if not char_url:
                continue
            char_resp = requests.get(f"https://api.ionq.co/v0.3{char_url}", headers=headers, timeout=15)
            char = char_resp.json() if char_resp.status_code == 200 else {}
            fidelity = char.get("fidelity", {})
            timing = char.get("timing", {})
            devices.append({
                "name": name, "location": b.get("location"),
                "qubits": b.get("qubits"), "queue_time_minutes": b.get("average_queue_time"),
                "two_qubit_fidelity_mean": fidelity.get("2q", {}).get("mean"),
                "one_qubit_fidelity_mean": fidelity.get("1q", {}).get("mean"),
                "spam_fidelity_mean": fidelity.get("spam", {}).get("mean"),
                "t1_seconds": timing.get("t1"), "t2_seconds": timing.get("t2"),
                "two_qubit_gate_seconds": timing.get("2q"),
            })
        devices.sort(key=lambda d: (d.get("two_qubit_fidelity_mean") or 0), reverse=True)
        for rank, d in enumerate(devices, 1):
            d["rank_by_two_qubit_fidelity"] = rank
        return {"devices": devices, "ranked_by": "two_qubit_fidelity_mean (highest first)"}
    except Exception as e:
        return {"error": str(e)}
