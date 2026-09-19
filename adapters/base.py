"""
QuantumBackendAdapter — the shared interface underneath providers/ibm.py and
providers/ionq.py.

Before this, core/verifier.py's own pipeline (topology_check,
hardware_aware_simulation, verify()) dispatched between IBM and IonQ via
scattered `if provider == "ionq": ... else: ...` branches, each with its own
inline `from providers.ibm import ...` / `from providers.ionq import ...`.
Every existing public function in providers/ibm.py and providers/ionq.py
keeps working unchanged — this interface sits underneath them, not instead
of them; concrete adapters are thin wrappers that call straight through.

Two families of required methods:
  - CRUD-shaped: list_devices, get_device_details, submit_job, job_status,
    job_results — device/job lifecycle operations.
  - verification-pipeline hooks: check_topology, simulate_hardware_aware —
    what used to be the scattered if/else in core/verifier.py.

Optional, capability-flagged methods (cancel_job, list_jobs, estimate_gates,
estimate_cost, preflight_check, cross_check_fidelity) raise
AdapterCapabilityError by default — a caller should check the corresponding
`supports_*` flag before calling, but any code path that doesn't still
fails loud and specific instead of an AttributeError or a silent no-op.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict, field


class AdapterCapabilityError(NotImplementedError):
    """Raised when a caller invokes a capability this provider's adapter doesn't support."""

    def __init__(self, provider_name: str, capability: str):
        super().__init__(
            f"'{provider_name}' adapter does not support '{capability}'. "
            f"Check the adapter's `supports_{capability}` flag before calling this method."
        )
        self.provider_name = provider_name
        self.capability = capability


def _to_dict_no_none(dc) -> dict:
    return {k: v for k, v in asdict(dc).items() if v is not None}


@dataclass
class DeviceInfo:
    name: str
    provider: str  # "ibm" | "ionq"
    num_qubits: int | None = None
    operational: bool | None = None
    status_message: str | None = None
    pending_jobs: int | None = None
    device_type: str | None = None  # "simulator" | "hardware"
    technology: str | None = None  # e.g. "trapped-ion" (IonQ only; IBM's API doesn't expose this)
    raw: dict | None = None

    def to_dict(self) -> dict:
        return _to_dict_no_none(self)


@dataclass
class JobSubmission:
    job_id: str
    provider: str
    backend: str
    status: str
    shots: int | None = None
    is_real_hardware: bool | None = None
    num_circuits: int | None = None
    self_check: dict | None = None
    raw: dict | None = None

    def to_dict(self) -> dict:
        return _to_dict_no_none(self)


@dataclass
class JobStatusInfo:
    job_id: str
    provider: str
    status: str
    backend: str | None = None
    queue_position: int | None = None
    error_message: str | None = None
    raw: dict | None = None

    def to_dict(self) -> dict:
        return _to_dict_no_none(self)


@dataclass
class JobResultData:
    job_id: str
    provider: str
    status: str
    counts: dict | list | None = None
    total_shots: int | None = None
    is_real_hardware: bool | None = None
    raw: dict | None = None

    def to_dict(self) -> dict:
        return _to_dict_no_none(self)


class QuantumBackendAdapter(ABC):
    provider_name: str

    # Capability flags — declared per-subclass, checked by callers before
    # calling the corresponding optional method.
    supports_cancel_job: bool = False
    supports_list_jobs: bool = False
    supports_estimate_gates: bool = False
    supports_estimate_cost: bool = False
    supports_preflight_check: bool = False
    supports_fidelity_cross_check: bool = False
    has_topology_risk: bool = True  # IBM: True (heavy-hex); IonQ: False (all-to-all)

    # --- Required: CRUD-shaped ---
    @abstractmethod
    def list_devices(self):
        ...

    @abstractmethod
    def get_device_details(self, device_name: str):
        ...

    @abstractmethod
    def submit_job(self, device_name: str, qasm_circuits, shots: int = 1024, **kwargs):
        ...

    @abstractmethod
    def job_status(self, job_id: str, **kwargs):
        ...

    @abstractmethod
    def job_results(self, job_id: str, **kwargs):
        ...

    # --- Required: verification-pipeline hooks ---
    @abstractmethod
    def check_topology(self, circuit) -> dict:
        ...

    @abstractmethod
    def simulate_hardware_aware(self, circuit, target_device: str, shots: int = 4096) -> dict:
        ...

    # --- Optional, capability-flagged ---
    def cancel_job(self, job_id: str) -> dict:
        raise AdapterCapabilityError(self.provider_name, "cancel_job")

    def list_jobs(self, limit: int = 10):
        raise AdapterCapabilityError(self.provider_name, "list_jobs")

    def estimate_gates(self, qasm_string: str, backend_name: str, optimization_level: int = 1) -> dict:
        raise AdapterCapabilityError(self.provider_name, "estimate_gates")

    def estimate_cost(self, qasm_circuits, shots: int = 4096) -> dict:
        raise AdapterCapabilityError(self.provider_name, "estimate_cost")

    def preflight_check(self, qasm_circuits, target_device: str, shots: int = 2048, **kwargs) -> dict:
        raise AdapterCapabilityError(self.provider_name, "preflight_check")

    def cross_check_fidelity(self, circuit, hw_result: dict, shots: int = 4096) -> dict:
        raise AdapterCapabilityError(self.provider_name, "cross_check_fidelity")
