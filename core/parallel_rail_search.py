"""
Generic parallel-rail search tiler -- takes N independently-built quantum
circuits, each solving a separate instance of some Ising/QUBO-shaped
search problem, and gets them onto real hardware efficiently in ONE job
instead of N separate submissions.

This generalizes quantum-hardware-mcp's run_parallel_collision_search (a
real, working, IBM-only tool that bundled Singmaster's-specific circuit-
building together with the tiling mechanic, and duplicated that same
tiling logic a second time in encode_4way_collision). Here, the tiler
knows nothing about what problem a rail is solving -- it only knows how
to place/submit circuits and hand each rail's counts back to that rail's
own decoder. A Singmaster's collision search (core/singmaster_search.py)
is the first thing plugged into it; graph coloring or anything else
Ising-shaped can plug in later with zero changes to this file.

The real, confirmed architectural fact this design is built around: IBM
and IonQ do not tile the same way.
  - IonQ already has real native multi-circuit batching
    (providers/ionq.py's ionq_submit_job -- a real list of separate
    circuits submitted as one job, with the $168.20 per-job floor
    confirmed shared across everything in the batch, not paid per
    circuit). No qubit-tiling needed at all here -- just pass a list.
  - IBM has no native batching -- providers/ibm.py's submit_job tiles
    multiple circuits into ONE combined circuit via qubit-offset
    placement (see providers/ibm.py's _tile_circuits) before submitting,
    since that's the only way to get "N searches, one IBM job" given
    IBM's real API shape as used in this project.
  - PennyLane/cudaq are free, local, synchronous -- no real cost floor to
    share, so no batching optimization is attempted; each rail is simply
    submitted in sequence.
"""
from adapters.registry import get_adapter


def run_parallel_rail_search(rail_specs: list, provider: str, target_device: str,
                              shots: int = 4096, **kwargs) -> dict:
    """
    Args:
        rail_specs: list of {"circuit": QuantumCircuit, "decode": callable(counts) -> dict,
            "label": str} -- the tiler never inspects what a rail's circuit or
            decoder actually do, it only places/submits circuits and routes
            each rail's counts to that rail's own decoder.
        provider: "ibm" | "ionq" | "pennylane" | "cudaq"
        target_device: passed straight through to the adapter's submit_job.
        shots: shots per rail (IBM: the combined circuit's shot count applies
            to every rail simultaneously, since they share one job; IonQ/
            PennyLane/cudaq: each rail genuinely gets its own shot count).

    Returns {"provider", "target_device", "rails": [{"label", "decoded"}, ...],
    "best_rail"} -- same "one call, full audited breakdown" spirit as
    run_hybrid_reduction_loop's round log.
    """
    if not rail_specs:
        return {"error": "rail_specs must be a non-empty list."}

    from qiskit.qasm2 import dumps as qasm2_dumps

    adapter = get_adapter(provider)

    if provider == "ibm":
        rails_out = _run_ibm_tiled(rail_specs, adapter, target_device, shots, qasm2_dumps, **kwargs)
    elif provider == "ionq":
        rails_out = _run_ionq_batched(rail_specs, adapter, target_device, shots, qasm2_dumps, **kwargs)
    else:
        rails_out = _run_sequential(rail_specs, adapter, target_device, shots, qasm2_dumps, **kwargs)

    if isinstance(rails_out, dict) and "error" in rails_out:
        return rails_out

    best_rail = None
    for r in rails_out:
        decoded = r.get("decoded", {})
        if "error" in decoded:
            continue
        amp = decoded.get("amplification")
        if amp is not None and (best_rail is None or amp > best_rail["decoded"].get("amplification", -1)):
            best_rail = r

    return {
        "provider": provider, "target_device": target_device,
        "num_rails": len(rail_specs), "rails": rails_out,
        "best_rail": best_rail,
    }


def _run_ibm_tiled(rail_specs, adapter, target_device, shots, qasm2_dumps, **kwargs):
    """
    Tile all rails into one combined circuit (real mechanics live in
    providers/ibm.py's _tile_circuits, reused here via the adapter's
    submit_job rather than duplicated), submit once, then slice the
    aggregate result back into each rail's own counts using the same
    MSB-from-the-end slicing already proven correct in
    quantum-hardware-mcp's run_parallel_collision_search and confirmed
    directly against a real 2-rail run before this was written.
    """
    qasm_list = [qasm2_dumps(spec["circuit"]) for spec in rail_specs]
    submission = adapter.submit_job(target_device, qasm_list, shots=shots, **kwargs)
    if "error" in submission:
        return {"error": submission["error"]}

    rail_ranges = submission.get("rail_ranges")
    if rail_ranges is None:
        # Single rail -- no tiling happened, the whole result belongs to it.
        rail_ranges = [(0, rail_specs[0]["circuit"].num_qubits)]

    results = adapter.job_results(submission["job_id"])
    if "error" in results:
        return {"error": results["error"]}

    counts = results.get("counts", {})
    total_qubits = sum(nq for _, nq in rail_ranges)

    rails_out = []
    for spec, (offset, nq) in zip(rail_specs, rail_ranges):
        rail_counts = {}
        for full_bits, c in counts.items():
            start = total_qubits - offset - nq
            end = total_qubits - offset
            slice_bits = full_bits[start:end]
            rail_counts[slice_bits] = rail_counts.get(slice_bits, 0) + c
        decoded = spec["decode"](rail_counts)
        rails_out.append({"label": spec.get("label"), "decoded": decoded})
    return rails_out


def _run_ionq_batched(rail_specs, adapter, target_device, shots, qasm2_dumps, **kwargs):
    """
    No qubit tiling -- IonQ's real API natively batches a list of
    separate circuits into one job, sharing the $168.20 per-job floor
    across all of them. Each rail stays its own independent circuit;
    each returned circuit's own counts map directly to its rail.

    Real, confirmed behavior worth being explicit about: when target_device
    resolves to the free "ionq_simulator" backend, ionq_submit_job doesn't
    return an async job_id at all -- it runs a synchronous self-check and
    returns immediately, with each circuit's counts already embedded under
    self_check.per_circuit, but truncated to only the top 5 states
    (providers/ionq.py's own returned payload, not this repo's choice to
    truncate further). That's real, deliberate behavior in that function
    (a lightweight pre-flight check, not meant to produce a full
    distribution), not something to work around by reaching into internals
    -- so this path decodes from it honestly, but every result explicitly
    flags itself as approximate for that reason, rather than silently
    reporting an amplification computed from an incomplete distribution as
    if it were exact. For a real, full-distribution result, target a real
    device (e.g. "qpu.forte-1") with confirm_real_hardware=True instead --
    that path returns a real job_id and job_results() returns the complete
    counts.
    """
    qasm_list = [qasm2_dumps(spec["circuit"]) for spec in rail_specs]
    submission = adapter.submit_job(target_device, qasm_list, shots=shots, **kwargs)
    if "error" in submission:
        return {"error": submission["error"]}

    if submission.get("status") == "SIMULATED" and "job_id" not in submission:
        per_circuit = submission.get("self_check", {}).get("per_circuit", [])
        rails_out = []
        for spec, sc in zip(rail_specs, per_circuit):
            top5_counts = sc.get("simulated_counts_top5", {})
            decoded = spec["decode"](top5_counts)
            decoded["approximate"] = True
            decoded["note"] = ("Decoded from the free simulator's self-check top-5 counts only, "
                                "not the full shot distribution -- a real target device with "
                                "confirm_real_hardware=True returns the complete counts instead.")
            rails_out.append({"label": spec.get("label"), "decoded": decoded})
        return rails_out

    results = adapter.job_results(submission["job_id"])
    if "error" in results:
        return {"error": results["error"]}

    counts_per_circuit = results.get("counts")
    if not isinstance(counts_per_circuit, list):
        # Some IonQ paths return one dict for a single-circuit job -- normalize.
        counts_per_circuit = [counts_per_circuit]

    rails_out = []
    for spec, rail_counts in zip(rail_specs, counts_per_circuit):
        decoded = spec["decode"](rail_counts or {})
        rails_out.append({"label": spec.get("label"), "decoded": decoded})
    return rails_out


def _run_sequential(rail_specs, adapter, target_device, shots, qasm2_dumps, **kwargs):
    """
    PennyLane/cudaq: free, local, synchronous -- no real cost floor to
    share, so no batching optimization is attempted. Each rail submitted
    and decoded in its own call.
    """
    rails_out = []
    for spec in rail_specs:
        qasm_string = qasm2_dumps(spec["circuit"])
        submission = adapter.submit_job(target_device, qasm_string, shots=shots, **kwargs)
        if "error" in submission:
            rails_out.append({"label": spec.get("label"), "decoded": {"error": submission["error"]}})
            continue
        results = adapter.job_results(submission["job_id"])
        if "error" in results:
            rails_out.append({"label": spec.get("label"), "decoded": {"error": results["error"]}})
            continue
        decoded = spec["decode"](results.get("counts", {}))
        rails_out.append({"label": spec.get("label"), "decoded": decoded})
    return rails_out
