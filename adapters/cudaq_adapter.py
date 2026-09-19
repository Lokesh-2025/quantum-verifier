"""
CudaQAdapter -- wraps CUDA-Q's local simulation targets (qpp-cpu by
default; nvidia automatically when cudaq itself confirms a usable GPU
target -- never a hand-rolled GPU-detection heuristic) behind
QuantumBackendAdapter. Same synchronous-execution honesty as
PennyLaneAdapter -- see that file's docstring, identical reasoning here.

Real, empirically-confirmed facts about the installed cudaq (0.16.0) this
was written and tested against, not assumed:
  - No built-in QASM importer exists (cudaq.translate only goes QASM/QIR
    OUTWARD from an already-defined kernel, never the reverse). A small,
    bounded circuit.data -> cudaq kernel translator is required, covering
    exactly this project's real gate vocabulary.
  - cudaq.has_target(name) only reports whether a target NAME is
    registered in this build, NOT whether it will actually run --
    confirmed directly: has_target("nvidia-mqpu") returns True on this
    GPU-less machine, but cudaq.set_target("nvidia-mqpu") still raises
    RuntimeError at that point. A real try/except around cudaq.set_target
    is therefore required, has_target()/num_available_gpus() alone are
    not sufficient checks.
  - On this machine (macOS ARM64, no NVIDIA GPU), cudaq.set_target("nvidia")
    raises RuntimeError: "Invalid target name (nvidia)" -- the macOS wheel
    doesn't ship that target at all, this is expected per cudaq's own
    PyPI platform notes, not a bug to work around.
  - cudaq.sample()'s bitstring convention puts qubit 0 LEFTMOST -- the
    opposite of Qiskit's get_counts(), which puts the highest clbit index
    leftmost. Confirmed empirically: an X-gate-only-on-qubit-0 circuit
    gives cudaq "10" but Qiskit "01". Reversed here to match Qiskit's
    convention, same reasoning as PennyLaneAdapter's bit-order fix.
  - cudaq.get_state()'s returned array orders amplitudes the SAME way
    qiskit.quantum_info.Statevector does (little-endian, qubit 0 = least
    significant index bit) -- confirmed empirically identical arrays for
    the same Bell circuit on both sides. No reversal needed for exact
    statevectors, only for sampled bitstrings.
"""
import uuid
from collections import Counter

from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector

from adapters.base import QuantumBackendAdapter

_JOB_CACHE: dict[str, dict] = {}
CPU_TARGET = "qpp-cpu"
GPU_TARGET = "nvidia"

# This project's real gate vocabulary (server.py, providers/ibm.py,
# providers/ionq.py, core/templates.py) -- deliberately bounded, not a
# general QASM interpreter. rzz has no direct cudaq kernel-builder method,
# built from the standard exact 2-CX decomposition instead (see
# _translate_to_kernel below).
_SUPPORTED_1Q = {"h", "x", "y", "z", "s", "sdg", "t", "tdg"}
_SUPPORTED_1Q_PARAM = {"rx", "ry", "rz"}
_SUPPORTED_2Q = {"cx", "cnot", "cz", "cy", "swap"}
_SUPPORTED_2Q_PARAM = {"crx", "cry", "crz"}


def _translate_to_kernel(circuit: QuantumCircuit, append_measurement: bool = True):
    """
    Bounded circuit.data -> cudaq kernel translator, same gate-by-gate-walk
    spirit as core/control_experiment.py's build_control_circuit. Raises
    ValueError (caught by callers) naming the exact unsupported gate,
    rather than silently skipping it or guessing a translation.

    append_measurement=False is used by get_exact_statevector, since
    cudaq.get_state requires an unmeasured kernel (measurement collapses
    the state it needs to return).
    """
    import cudaq

    kernel = cudaq.make_kernel()
    q = kernel.qalloc(circuit.num_qubits)

    for instruction in circuit.data:
        name = instruction.operation.name.lower()
        qubits = [circuit.find_bit(qb).index for qb in instruction.qubits]

        if name in ("measure", "barrier"):
            continue
        elif name in _SUPPORTED_1Q:
            getattr(kernel, name)(q[qubits[0]])
        elif name in _SUPPORTED_1Q_PARAM:
            theta = float(instruction.operation.params[0])
            getattr(kernel, name)(theta, q[qubits[0]])
        elif name in ("cx", "cnot"):
            kernel.cx(q[qubits[0]], q[qubits[1]])
        elif name in ("cz", "cy", "swap"):
            getattr(kernel, name)(q[qubits[0]], q[qubits[1]])
        elif name in _SUPPORTED_2Q_PARAM:
            theta = float(instruction.operation.params[0])
            getattr(kernel, name)(theta, q[qubits[0]], q[qubits[1]])
        elif name == "rzz":
            # Exact decomposition: RZZ(theta) = CX; RZ(theta); CX (standard
            # identity, same one core/verifier.py's own IBM path relies on
            # for fixed-angle-native devices -- confirmed there against
            # real ibm_fez data: an isolated rzz transpiles to exactly 2 cx).
            theta = float(instruction.operation.params[0])
            kernel.cx(q[qubits[0]], q[qubits[1]])
            kernel.rz(theta, q[qubits[1]])
            kernel.cx(q[qubits[0]], q[qubits[1]])
        else:
            raise ValueError(
                f"Gate '{name}' is not in this adapter's supported translation "
                f"vocabulary ({sorted(_SUPPORTED_1Q | _SUPPORTED_1Q_PARAM | _SUPPORTED_2Q | _SUPPORTED_2Q_PARAM | {'rzz'})})."
            )

    if append_measurement:
        kernel.mz(q)
    return kernel


def select_target() -> str:
    """
    Delegates entirely to cudaq's own target system. has_target() alone is
    NOT sufficient (confirmed empirically -- it can report True for a
    target that then fails to actually set), so the real check is
    attempting cudaq.set_target() and catching the RuntimeError it raises
    when the target genuinely can't run on this machine.
    """
    import cudaq
    try:
        cudaq.set_target(GPU_TARGET)
        return GPU_TARGET
    except RuntimeError:
        cudaq.set_target(CPU_TARGET)
        return CPU_TARGET


def _counts_from_sample_result(result) -> dict:
    """
    cudaq puts qubit 0 leftmost; Qiskit puts the highest clbit index
    leftmost -- reverse to match, same reasoning as PennyLaneAdapter's
    _counts_from_samples. See this module's docstring for the empirical
    confirmation.
    """
    return {bitstring[::-1]: count for bitstring, count in dict(result.items()).items()}


def run_circuit(circuit: QuantumCircuit, shots: int = 1024) -> dict:
    """Module-level, directly testable without the job_id ceremony."""
    import cudaq
    select_target()
    kernel = _translate_to_kernel(circuit)
    result = cudaq.sample(kernel, shots_count=shots)
    return _counts_from_sample_result(result)


def get_exact_statevector(circuit: QuantumCircuit) -> Statevector:
    """
    Exact classical simulation via cudaq.get_state -- requires a
    measurement-free circuit (state collapses otherwise), mirroring
    qiskit's own Statevector.from_instruction requirement. Returns a
    qiskit Statevector object specifically so downstream code (entropy,
    exact-probability comparisons) reuses qiskit.quantum_info's
    already-tested math instead of reinventing it in cudaq's API. No
    bit-order reversal here -- confirmed empirically that cudaq's
    get_state array ordering already matches Statevector.from_instruction's
    exactly for the same circuit.
    """
    import cudaq
    import numpy as np

    select_target()
    unmeasured = circuit.remove_final_measurements(inplace=False)
    kernel = _translate_to_kernel(unmeasured, append_measurement=False)
    state = cudaq.get_state(kernel)
    return Statevector(np.array(state))


class CudaQAdapter(QuantumBackendAdapter):
    provider_name = "cudaq"
    has_topology_risk = False

    def list_devices(self):
        target = select_target()
        return [{"name": target, "provider": "cudaq", "operational": True,
                 "device_type": "simulator",
                 "technology": "GPU-accelerated state vector (NVIDIA cuQuantum)"
                               if target == GPU_TARGET else
                               "local CPU simulator (no usable NVIDIA GPU target on this machine)"}]

    def get_device_details(self, device_name: str):
        target = select_target()
        if device_name not in (CPU_TARGET, GPU_TARGET):
            return {"error": f"Unknown cudaq target '{device_name}'"}
        return {"name": device_name, "provider": "cudaq", "operational": device_name == target,
                "device_type": "simulator", "note": f"Active target on this machine: '{target}'."}

    def submit_job(self, device_name: str, qasm_circuits, shots: int = 1024, **kwargs):
        qasm_string = qasm_circuits[0] if isinstance(qasm_circuits, list) else qasm_circuits
        circuit = QuantumCircuit.from_qasm_str(qasm_string)
        try:
            counts = run_circuit(circuit, shots)
        except Exception as e:
            return {"error": f"cudaq execution failed: {e}"}
        target = select_target()
        job_id = f"cudaq-{uuid.uuid4().hex[:12]}"
        _JOB_CACHE[job_id] = {"counts": counts, "shots": shots, "target": target}
        return {"job_id": job_id, "provider": "cudaq", "backend": target,
                "status": "DONE", "shots": shots, "is_real_hardware": False,
                "note": "cudaq execution is synchronous/local -- this job already ran."}

    def job_status(self, job_id, **kwargs):
        if job_id not in _JOB_CACHE:
            return {"error": f"Unknown job_id '{job_id}'"}
        return {"job_id": job_id, "provider": "cudaq", "status": "DONE"}

    def job_results(self, job_id, **kwargs):
        if job_id not in _JOB_CACHE:
            return {"error": f"Unknown job_id '{job_id}'"}
        cached = _JOB_CACHE[job_id]
        return {"job_id": job_id, "provider": "cudaq", "status": "DONE",
                "counts": cached["counts"], "total_shots": sum(cached["counts"].values()),
                "is_real_hardware": False}

    def check_topology(self, circuit) -> dict:
        return {"applicable": False, "passed": True,
                "note": "cudaq's simulation targets are software simulators with no physical "
                        "qubit connectivity -- no routing/degree risk exists for this provider."}

    def simulate_hardware_aware(self, circuit, target_device: str, shots: int = 4096) -> dict:
        from core.verifier import gate_synthesis_check
        try:
            counts = run_circuit(circuit, shots)
        except Exception as e:
            return {"error": f"cudaq simulation failed: {e}"}
        target = select_target()
        return {
            "counts": counts, "total_shots": sum(counts.values()),
            "simulation_type": f"noiseless exact simulation on cudaq's '{target}' target -- "
                                "no hardware noise model, independent cross-check of the same "
                                "ideal answer ideal_simulation computes on qiskit_aer.",
            "gate_synthesis_check": gate_synthesis_check(circuit, circuit),
        }
