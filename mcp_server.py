"""
quantum-verifier MCP server.

Thin wrapper — every real capability lives in core/ and providers/, which
have zero MCP dependency and can be imported and used directly (in tests,
scripts, another agent's code) without ever going through this file. This
module's only job is exposing those functions as MCP tools.
"""
import json
from mcp.server.fastmcp import FastMCP

from core.verifier import verify as _verify
from core.control_experiment import falsify as _falsify
from core.robustness import find_robust_circuit as _find_robust_circuit
from core.memory import memory_summary as _memory_summary
import providers.ibm as ibm
import providers.ionq as ionq

mcp = FastMCP("quantum-verifier")


# --------------------------------------------------------------------------
# Core: the Verifier and the control-experiment generator
# --------------------------------------------------------------------------

@mcp.tool()
def verify_experiment(
    qasm_string: str,
    provider: str,
    target_device: str,
    shots: int = 4096,
    expected_marked_bitstrings: list = None,
    expected_amplification: float = None,
    amplification_tolerance: float = 0.5,
) -> str:
    """
    The safety gate. Checks a circuit's semantics, its routing/topology risk,
    an ideal simulation, a hardware-aware simulation (real noise model on
    IonQ; live-calibration fidelity estimate on IBM), and — if a known
    answer is supplied — whether the claimed result is actually
    distinguishable from predicted hardware behavior.

    Args:
        qasm_string   : OpenQASM 2.0 circuit string
        provider      : "ibm" or "ionq"
        target_device : e.g. "ibm_fez", "forte-1", "simulator"
        shots         : shots for the simulation passes (default 4096)
        expected_marked_bitstrings : optional, target bitstrings for a known-answer check
        expected_amplification     : optional, predicted amplification to verify against
        amplification_tolerance    : relative tolerance (default 0.5)

    Returns a GO/BLOCK verdict with structured, human-readable reasons.
    If no expected result is supplied, use falsify_claim instead — it
    doesn't require knowing the answer in advance.
    """
    return json.dumps(_verify(
        qasm_string, provider, target_device, shots,
        expected_marked_bitstrings, expected_amplification, amplification_tolerance,
    ), indent=2)


@mcp.tool()
def falsify_claim(
    qasm_string: str,
    provider: str,
    target_device: str,
    marked_bitstrings: list = None,
    shots: int = 4096,
) -> str:
    """
    Automatically generates a control circuit — the same circuit with its
    entangling gates removed — and runs both through the same hardware-aware
    simulation. The difference between them is the real, confound-isolated
    effect size, with SPAM/readout bias (which affects both circuits
    equally) subtracted out.

    Works even without a known answer (true discovery-mode research) —
    without marked_bitstrings, reports which bitstrings gained the most
    probability from adding entanglement, and the overall distributional
    distance between the real circuit and its control.

    Args:
        qasm_string        : OpenQASM 2.0 circuit string, must contain at
                              least one entangling (two-qubit) gate
        provider            : "ibm" or "ionq" (control-circuit comparison
                              currently only returns counts on the IonQ path
                              — IBM's hardware-aware simulation is a fidelity
                              estimate, not raw counts)
        target_device       : e.g. "forte-1", "simulator"
        marked_bitstrings   : optional — if you have a specific claimed
                              target, isolates its real effect size
        shots               : shots per circuit (default 4096)
    """
    return json.dumps(_falsify(qasm_string, provider, target_device, marked_bitstrings, shots), indent=2)


# --------------------------------------------------------------------------
# IBM provider
# --------------------------------------------------------------------------

@mcp.tool()
def list_devices() -> str:
    """All accessible IBM backends with live operational status."""
    return json.dumps(ibm.list_devices(), indent=2)


@mcp.tool()
def get_device_details(device_name: str) -> str:
    """Per-qubit T1/T2, readout error, gate error, queue depth."""
    return json.dumps(ibm.get_device_details(device_name), indent=2)


@mcp.tool()
def best_qubits(device_name: str, n: int = 5) -> str:
    """Best n individual qubits on a device by live calibration."""
    return json.dumps(ibm.best_qubits(device_name, n), indent=2)


@mcp.tool()
def compare_devices(sort_by: str = "cx_error") -> str:
    """Rank IBM devices by cx_error, queue, qubits, or combined score."""
    return json.dumps(ibm.compare_devices(sort_by), indent=2)


@mcp.tool()
def queue_status() -> str:
    """Current queue snapshot across all IBM backends."""
    return json.dumps(ibm.queue_status(), indent=2)


@mcp.tool()
def device_history(device_name: str, days: int = 7) -> str:
    """Calibration snapshots over the last N days."""
    return json.dumps(ibm.device_history(device_name, days), indent=2)


@mcp.tool()
def device_profile(device_name: str) -> str:
    """Complete hardware profile from the most recent snapshot."""
    return json.dumps(ibm.device_profile(device_name), indent=2)


@mcp.tool()
def device_on_date(device_name: str, date: str) -> str:
    """Historical stats for a device on a specific past date."""
    return json.dumps(ibm.device_on_date(device_name, date), indent=2)


@mcp.tool()
def submit_job(device_name: str, qasm_string: str, shots: int = 1024, qasm_version: int = 2) -> str:
    """Compile and submit a circuit to an IBM quantum computer. Prefer calling verify_experiment first."""
    return json.dumps(ibm.submit_job(device_name, qasm_string, shots, qasm_version), indent=2)


@mcp.tool()
def job_status(job_id: str) -> str:
    """Status of a submitted IBM job."""
    return json.dumps(ibm.job_status(job_id), indent=2)


@mcp.tool()
def job_results(job_id: str) -> str:
    """Measurement counts from a completed IBM job."""
    return json.dumps(ibm.job_results(job_id), indent=2)


@mcp.tool()
def cancel_job(job_id: str) -> str:
    """Cancel a queued or running IBM job."""
    return json.dumps(ibm.cancel_job(job_id), indent=2)


@mcp.tool()
def list_jobs(limit: int = 10) -> str:
    """Most recently submitted IBM jobs."""
    return json.dumps(ibm.list_jobs(limit), indent=2)


@mcp.tool()
def estimate_runtime(circuit: str, backend_name: str, shots: int = 1024) -> str:
    """Estimate IBM QPU minutes for a circuit before submitting."""
    return json.dumps(ibm.estimate_runtime(circuit, backend_name, shots), indent=2)


@mcp.tool()
def route_job(circuit: str, shots: int = 1024, max_minutes: float = 10.0) -> str:
    """Recommend the best IBM device for a circuit based on cost and quality."""
    return json.dumps(ibm.route_job(circuit, shots, max_minutes), indent=2)


@mcp.tool()
def get_alerts(device_name: str = "", days: int = 7) -> str:
    """Calibration drift alerts for IBM devices."""
    return json.dumps(ibm.get_alerts(device_name, days), indent=2)


@mcp.tool()
def start_repro_experiment(circuit: str, backend_name: str, n_runs: int = 5, shots: int = 1024) -> str:
    """Submit the same circuit N times to measure reproducibility on real IBM hardware."""
    return json.dumps(ibm.start_repro_experiment(circuit, backend_name, n_runs, shots), indent=2)


@mcp.tool()
def repro_score(experiment_id: int) -> str:
    """0-1 reproducibility score after repeat runs complete."""
    return json.dumps(ibm.repro_score(experiment_id), indent=2)


@mcp.tool()
def job_analytics() -> str:
    """Breakdown of jobs submitted through this server, by tool."""
    return json.dumps(ibm.job_analytics(), indent=2)


# --------------------------------------------------------------------------
# IonQ provider
# --------------------------------------------------------------------------

@mcp.tool()
def ionq_devices() -> str:
    """All IonQ backends and simulators with live status."""
    return json.dumps(ionq.ionq_devices(), indent=2)


@mcp.tool()
def ionq_submit_job(
    backend_name: str,
    qasm_circuits: list,
    shots: int = 1024,
    optimization_level: int = 1,
    expected_marked_bitstrings: list = None,
    expected_amplification=None,
    amplification_tolerance: float = 0.5,
    confirm_real_hardware: bool = False,
) -> str:
    """
    Submit one or more circuits to IonQ as a batched job, with a mandatory
    self-check against the real target device's noise model before
    anything is billed. Prefer calling verify_experiment first.
    """
    return json.dumps(ionq.ionq_submit_job(
        backend_name, qasm_circuits, shots, optimization_level,
        expected_marked_bitstrings, expected_amplification,
        amplification_tolerance, confirm_real_hardware,
    ), indent=2)


@mcp.tool()
def ionq_job_status(job_id: str, backend_name: str = "ionq_simulator") -> str:
    """Status of a submitted IonQ job."""
    return json.dumps(ionq.ionq_job_status(job_id, backend_name), indent=2)


@mcp.tool()
def ionq_job_results(job_id: str, backend_name: str = "simulator") -> str:
    """Measurement counts from a completed IonQ job."""
    return json.dumps(ionq.ionq_job_results(job_id, backend_name), indent=2)


@mcp.tool()
def estimate_ionq_gates(qasm_string: str, backend_name: str = "forte-1", optimization_level: int = 1) -> str:
    """Native gate count (GPI/GPI2/ZZ) for a circuit before submitting."""
    return json.dumps(ionq.estimate_ionq_gates(qasm_string, backend_name, optimization_level), indent=2)


@mcp.tool()
def estimate_ionq_cost(qasm_circuits: list, shots: int = 4096) -> str:
    """Dollar cost preview using IonQ's real per-job pricing floor."""
    return json.dumps(ionq.estimate_ionq_cost(qasm_circuits, shots), indent=2)


@mcp.tool()
def ibm_account_check() -> str:
    """
    Which IBM Quantum instance(s) this account can access and real usage
    quota status (seconds of QPU time on the free plan — a genuinely
    different model from IonQ's dollar budgets, checked directly against
    IBM's API rather than assumed).
    """
    return json.dumps(ibm.ibm_account_check(), indent=2)


@mcp.tool()
def ionq_preflight(
    qasm_circuits: list,
    target_device: str,
    shots: int = 2048,
    expected_marked_bitstrings: list = None,
    expected_amplification=None,
    amplification_tolerance: float = 0.5,
) -> str:
    """
    Runs the full recommended sequence before a real IonQ submission in
    ONE call, instead of remembering to call account check, device
    comparison, verify_experiment, and a budget check separately in the
    right order: account/budget check, device standing, per-circuit
    safety verification, and real cost vs. remaining budget. Returns one
    clear GO/BLOCK verdict. Does not submit anything — pass the same
    arguments to ionq_submit_job (with confirm_real_hardware=True) once
    this returns GO.
    """
    return json.dumps(ionq.ionq_preflight(
        qasm_circuits, target_device, shots,
        expected_marked_bitstrings, expected_amplification, amplification_tolerance,
    ), indent=2)


@mcp.tool()
def ionq_account_check() -> str:
    """
    Which IonQ project(s)/organization the current API key can actually
    submit to, and their real budget status. Run this before assuming
    "my API key = my funded organization" — built after this project spent
    an unknown stretch of time pointed at the wrong, unfunded organization
    with the real, funded one never even having a key generated for it.
    """
    return json.dumps(ionq.ionq_account_check(), indent=2)


@mcp.tool()
def ionq_compare_devices() -> str:
    """
    Ranks IonQ's real hardware devices by live calibration data (2-qubit
    fidelity, coherence time, gate speed) instead of picking one out of
    habit. IonQ equivalent of the IBM-side compare_devices tool.
    """
    return json.dumps(ionq.ionq_compare_devices(), indent=2)


@mcp.tool()
def find_robust_circuit(
    candidate_qasm_circuits: list,
    provider: str,
    target_device: str,
    marked_bitstrings: list,
    shots: int = 2048,
    n_scoring_runs: int = 2,
    variance_penalty: float = 1.0,
) -> str:
    """
    Given several candidate circuits for the same problem (e.g. the same
    circuit with different tunable parameters), picks the one that's most
    robust to REAL target-device noise — not just whichever scores highest
    in a perfect, noiseless simulation. Scores each candidate across
    multiple independent real-noise runs (mean minus a penalty for
    inconsistency between runs), then validates the winner on one more
    fresh run it wasn't scored on.

    Use this whenever choosing between parameter settings for the same
    circuit — picking by ideal/noiseless score alone can select a fragile
    point that looks great on paper and falls apart on real hardware
    (this happened for real in this project: a circuit tuned for best
    noiseless performance was ~2.5x weaker on real hardware than a
    lower-scoring-on-paper alternative found this way).
    """
    return json.dumps(_find_robust_circuit(
        candidate_qasm_circuits, provider, target_device, marked_bitstrings,
        shots, n_scoring_runs, variance_penalty,
    ), indent=2)


# --------------------------------------------------------------------------
# Experiment Memory — prediction-vs-reality tracking
# --------------------------------------------------------------------------

@mcp.tool()
def ionq_sync_memory_for_job(job_id: str) -> str:
    """
    Completes Experiment Memory for a real IonQ job once it's actually
    finished: fetches its real results and records the real amplification
    against the prediction ionq_submit_job made automatically at
    submission time. Call this after a job you submitted through
    ionq_submit_job has completed.
    """
    return json.dumps(ionq.ionq_sync_memory_for_job(job_id), indent=2)


@mcp.tool()
def memory_summary(provider: str = None) -> str:
    """
    How trustworthy has this tool's predictions actually been, broken
    down by provider and target device — computed from real recorded
    prediction-vs-reality pairs, not a guess. Small sample sizes should
    be read with real caution.
    """
    return json.dumps(_memory_summary(provider), indent=2)


if __name__ == "__main__":
    mcp.run()
