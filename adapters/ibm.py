"""
IBMAdapter — wraps providers/ibm.py's functions and core/verifier.py's IBM
verification-pipeline logic behind the QuantumBackendAdapter interface.

check_topology / simulate_hardware_aware bodies are moved verbatim from
core/verifier.py's topology_check / hardware_aware_simulation IBM branches —
same logic, same behavior, just relocated so core/verifier.py no longer
needs to know which provider it's talking to.
"""
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

import providers.ibm as ibm
from providers.ibm import _get_service, _cx_errors_for_backend
from adapters.base import QuantumBackendAdapter, AdapterCapabilityError

HEAVY_HEX_MAX_DEGREE = 3


class IBMAdapter(QuantumBackendAdapter):
    provider_name = "ibm"
    has_topology_risk = True
    supports_cancel_job = True
    supports_list_jobs = True
    supports_fidelity_cross_check = True

    # --- CRUD, pure pass-throughs ---
    def list_devices(self):
        return ibm.list_devices()

    def get_device_details(self, device_name: str):
        return ibm.get_device_details(device_name)

    def submit_job(self, device_name: str, qasm_circuits, shots: int = 1024, **kwargs):
        # providers.ibm.submit_job now genuinely accepts a list -- more than
        # one circuit gets tiled into a single combined job via qubit-offset
        # placement. Forward the real list, don't silently drop anything
        # past index 0 the way this used to.
        return ibm.submit_job(device_name, qasm_circuits, shots, **kwargs)

    def job_status(self, job_id: str, **kwargs):
        return ibm.job_status(job_id)

    def job_results(self, job_id: str, **kwargs):
        return ibm.job_results(job_id)

    def cancel_job(self, job_id: str):
        return ibm.cancel_job(job_id)

    def list_jobs(self, limit: int = 10):
        return ibm.list_jobs(limit)

    # --- verification-pipeline hooks, moved verbatim from core/verifier.py ---
    def check_topology(self, circuit) -> dict:
        """
        IBM heavy-hex caps any qubit at 3 direct interaction partners —
        exceeding it forces SWAP injection and can 4x+ the real gate count
        (the exact failure this project hit and documented).
        """
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

    def simulate_hardware_aware(self, circuit, target_device: str, shots: int = 4096) -> dict:
        """
        A fidelity ESTIMATE from real, live calibration data
        (product-of-gate-errors across the transpiled circuit's 2-qubit
        gates), plus a real noisy simulation built from that same backend's
        live calibration data (qiskit_aer NoiseModel.from_backend). IBM's
        public API doesn't expose a per-device noise model the way IonQ's
        does — that asymmetry is real and documented, not papered over.
        """
        from core.verifier import gate_synthesis_check

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

    def cross_check_fidelity(self, circuit, hw_result: dict, shots: int = 4096) -> dict:
        from core.verifier import cross_check_fidelity_estimate
        return cross_check_fidelity_estimate(circuit, hw_result, shots)
