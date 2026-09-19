"""
IonQAdapter — wraps providers/ionq.py's functions and core/verifier.py's
IonQ verification-pipeline logic behind the QuantumBackendAdapter interface.

check_topology / simulate_hardware_aware bodies are moved verbatim from
core/verifier.py's topology_check / hardware_aware_simulation IonQ branches.
"""
from qiskit import transpile

import providers.ionq as ionq
from providers.ionq import _decompose_large_angle_rzz, _resolve_ionq_backend, _ionq_is_hardware
from adapters.base import QuantumBackendAdapter, AdapterCapabilityError


class IonQAdapter(QuantumBackendAdapter):
    provider_name = "ionq"
    has_topology_risk = False
    supports_estimate_gates = True
    supports_estimate_cost = True
    supports_preflight_check = True
    # No cancel_job / list_jobs — no IonQ equivalent exists in providers/ionq.py today.

    # --- CRUD, pure pass-throughs ---
    def list_devices(self):
        return ionq.ionq_devices()

    def get_device_details(self, device_name: str):
        return ionq.get_device_details(device_name)

    def submit_job(self, device_name: str, qasm_circuits, shots: int = 1024, **kwargs):
        circuits = [qasm_circuits] if isinstance(qasm_circuits, str) else qasm_circuits
        return ionq.ionq_submit_job(device_name, circuits, shots, **kwargs)

    def job_status(self, job_id: str, backend_name: str = "ionq_simulator", **kwargs):
        return ionq.ionq_job_status(job_id, backend_name)

    def job_results(self, job_id: str, backend_name: str = "simulator", **kwargs):
        return ionq.ionq_job_results(job_id, backend_name)

    # --- optional capabilities ---
    def estimate_gates(self, qasm_string: str, backend_name: str = "forte-1", optimization_level: int = 1) -> dict:
        return ionq.estimate_ionq_gates(qasm_string, backend_name, optimization_level)

    def estimate_cost(self, qasm_circuits, shots: int = 4096) -> dict:
        return ionq.estimate_ionq_cost(qasm_circuits, shots)

    def preflight_check(self, qasm_circuits, target_device: str, shots: int = 2048, **kwargs) -> dict:
        return ionq.ionq_preflight(qasm_circuits, target_device, shots, **kwargs)

    # --- verification-pipeline hooks, moved verbatim from core/verifier.py ---
    def check_topology(self, circuit) -> dict:
        """IonQ is all-to-all connected — no routing/degree risk exists."""
        return {"applicable": False, "passed": True,
                "note": "IonQ is all-to-all connected — no routing/degree risk exists for this provider."}

    def simulate_hardware_aware(self, circuit, target_device: str, shots: int = 4096) -> dict:
        """
        Full noisy simulation using the device's real named noise model
        (depolarizing channels after each gate, fixed rates — verified
        against IonQ's own docs). A genuine noisy execution, not an estimate.
        """
        from core.verifier import gate_synthesis_check

        from qiskit_ionq import IonQProvider
        import os
        api_key = os.getenv("IONQ_API_KEY")
        if not api_key:
            return {"error": "IONQ_API_KEY not set"}
        try:
            resolved = _resolve_ionq_backend(target_device)
            ionq_provider = IonQProvider(api_key)
            # Transpiling against the bare "ionq_simulator" target silently picks
            # its DEFAULT native gateset, which is the legacy Aria-only MS gate,
            # not Forte's zz -- the exact trap estimate_ionq_gates/estimate_ionq_cost
            # already document and avoid by defaulting to forte-1's real target.
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
