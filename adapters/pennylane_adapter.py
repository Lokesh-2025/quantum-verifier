"""
PennyLaneAdapter -- wraps PennyLane's local simulator devices behind
QuantumBackendAdapter. PennyLane has no async hardware-job concept the way
IBM/IonQ do: execution is synchronous and local. submit_job/job_status/
job_results are shaped to be HONEST about that, not to fake asynchronicity
that doesn't exist: submit_job executes immediately, job_status for any
job_id this adapter minted is always immediately "DONE", job_results
replays the already-computed result from an in-process cache.

Unlike IBM's job store, this cache does NOT persist across process
restarts and is not visible to any other process -- a real, inherent
asymmetry versus IBM/IonQ (where any process holding valid credentials can
look up any job_id), documented here rather than hidden.

Requires the separate `pennylane-qiskit` plugin package for QASM loading
(plain `pennylane` does not include a QASM importer) -- confirmed directly:
`qml.from_qasm` raises "Failed to load the qasm plugin. Please ensure that
the pennylane-qiskit package is installed." without it.
"""
import uuid
from collections import Counter

import pennylane as qml
from qiskit import QuantumCircuit
from qiskit.qasm2 import dumps as qasm2_dumps

from adapters.base import QuantumBackendAdapter

_JOB_CACHE: dict[str, dict] = {}

SUPPORTED_DEVICES = {
    "default.qubit": "local simulator (pure-Python state vector)",
    "lightning.qubit": "local simulator (C++ state vector, faster than default.qubit)",
}
DEFAULT_DEVICE = "default.qubit"


def _counts_from_samples(samples) -> dict:
    """
    PennyLane's qml.sample returns one row per shot, columns ordered by
    ASCENDING wire index (wire 0 first). Qiskit's get_counts() bitstrings
    put the HIGHEST clbit index leftmost -- confirmed empirically these
    are genuinely reversed conventions (an X-gate-only-on-qubit-0 circuit
    gives PennyLane's raw columns [1, 0] but Qiskit's get_counts() gives
    "01"). Reversing per-shot column order here is required so this
    adapter's "counts" mean the same qubit->character-position mapping
    IBM's/IonQ's adapters already produce -- otherwise ground_truth_check /
    control_experiment.falsify's marked_bitstrings comparisons would
    silently compare against the wrong convention. See
    tests/adapters/test_pennylane_adapter.py's asymmetric-circuit test,
    which exists specifically to catch this class of bug (the same class
    detect_reversed_bitstring_convention already guards against elsewhere
    in this pipeline).
    """
    bitstrings = ["".join(str(int(b)) for b in reversed(row)) for row in samples]
    return dict(Counter(bitstrings))


def run_circuit(circuit: QuantumCircuit, device_name: str = DEFAULT_DEVICE, shots: int = 1024) -> dict:
    """
    Module-level, not just adapter-private -- directly testable without
    going through the job_id ceremony, and reusable by anything else that
    wants a plain PennyLane execution of a Qiskit circuit.
    """
    if device_name not in SUPPORTED_DEVICES:
        device_name = DEFAULT_DEVICE
    if circuit.num_clbits == 0:
        raise ValueError("Circuit has no classical bits to measure into.")

    qasm_string = qasm2_dumps(circuit)
    dev = qml.device(device_name, wires=circuit.num_qubits)
    qfunc = qml.from_qasm(qasm_string)

    @qml.qnode(dev)
    def _run():
        qfunc()
        return qml.sample(wires=list(range(circuit.num_qubits)))

    shot_qnode = qml.set_shots(_run, shots=shots)
    samples = shot_qnode()
    if circuit.num_qubits == 1:
        samples = samples.reshape(-1, 1)
    return _counts_from_samples(samples)


class PennyLaneAdapter(QuantumBackendAdapter):
    provider_name = "pennylane"
    has_topology_risk = False

    def list_devices(self):
        return [
            {"name": name, "provider": "pennylane", "operational": True,
             "device_type": "simulator", "technology": tech}
            for name, tech in SUPPORTED_DEVICES.items()
        ]

    def get_device_details(self, device_name: str):
        if device_name not in SUPPORTED_DEVICES:
            return {"error": f"Unknown PennyLane device '{device_name}' -- "
                              f"expected one of {sorted(SUPPORTED_DEVICES)}"}
        return {"name": device_name, "provider": "pennylane", "operational": True,
                "device_type": "simulator", "technology": SUPPORTED_DEVICES[device_name]}

    def submit_job(self, device_name: str, qasm_circuits, shots: int = 1024, **kwargs):
        qasm_string = qasm_circuits[0] if isinstance(qasm_circuits, list) else qasm_circuits
        circuit = QuantumCircuit.from_qasm_str(qasm_string)
        try:
            counts = run_circuit(circuit, device_name, shots)
        except Exception as e:
            return {"error": f"PennyLane execution failed: {e}"}
        job_id = f"pennylane-{uuid.uuid4().hex[:12]}"
        _JOB_CACHE[job_id] = {"counts": counts, "shots": shots, "device": device_name}
        return {"job_id": job_id, "provider": "pennylane", "backend": device_name,
                "status": "DONE", "shots": shots, "is_real_hardware": False,
                "note": "PennyLane execution is synchronous/local -- this job already ran; "
                        "job_status/job_results replay this same result, they do not poll anything."}

    def job_status(self, job_id: str, **kwargs):
        if job_id not in _JOB_CACHE:
            return {"error": f"Unknown job_id '{job_id}'"}
        return {"job_id": job_id, "provider": "pennylane", "status": "DONE"}

    def job_results(self, job_id: str, **kwargs):
        if job_id not in _JOB_CACHE:
            return {"error": f"Unknown job_id '{job_id}'"}
        cached = _JOB_CACHE[job_id]
        return {"job_id": job_id, "provider": "pennylane", "status": "DONE",
                "counts": cached["counts"], "total_shots": sum(cached["counts"].values()),
                "is_real_hardware": False}

    def check_topology(self, circuit) -> dict:
        return {"applicable": False, "passed": True,
                "note": "PennyLane's simulator devices have no physical qubit connectivity "
                        "-- no routing/degree risk exists for this provider."}

    def simulate_hardware_aware(self, circuit, target_device: str, shots: int = 4096) -> dict:
        from core.verifier import gate_synthesis_check
        try:
            counts = run_circuit(circuit, target_device, shots)
        except Exception as e:
            return {"error": f"PennyLane simulation failed: {e}"}
        # No transpilation to a native gateset happens here (qml.from_qasm
        # maps gate-for-gate onto PennyLane's own operations, it doesn't
        # retarget to any device's native basis the way IBM/IonQ
        # transpilation does) -- gate_synthesis_check(circuit, circuit) is
        # therefore called against the SAME circuit on both sides,
        # deliberately trivially passing, purely to keep every adapter
        # honoring the same shared contract verify() depends on (it reads
        # hw.get("gate_synthesis_check") to decide a BLOCK gate).
        return {
            "counts": counts, "total_shots": sum(counts.values()),
            "simulation_type": f"noiseless simulation on PennyLane's {target_device} -- "
                                "there is no hardware noise model here because there is no "
                                "hardware; this is an independent cross-check of the same "
                                "ideal answer ideal_simulation computes on qiskit_aer.",
            "gate_synthesis_check": gate_synthesis_check(circuit, circuit),
        }
