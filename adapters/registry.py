"""
get_adapter(provider) — factory with a small instance cache.

Adapters are cheap/stateless per call (they call _get_service()/build
IonQProvider(api_key) fresh each time, matching today's behavior of
re-reading env/contextvar on every call). Caching the adapter *instance* is
safe; never cache a service/provider object inside one, since IBM's
use_ibm_token per-request override needs every call to re-check the current
contextvar.
"""
from adapters.base import QuantumBackendAdapter
from adapters.ibm import IBMAdapter
from adapters.ionq import IonQAdapter
from adapters.pennylane_adapter import PennyLaneAdapter
from adapters.cudaq_adapter import CudaQAdapter

_ADAPTER_CLASSES = {
    "ibm": IBMAdapter, "ionq": IonQAdapter,
    "pennylane": PennyLaneAdapter, "cudaq": CudaQAdapter,
}
_instances: dict[str, QuantumBackendAdapter] = {}


def get_adapter(provider: str) -> QuantumBackendAdapter:
    provider = provider.lower()
    if provider not in _ADAPTER_CLASSES:
        raise ValueError(f"Unknown provider '{provider}' — expected one of {sorted(_ADAPTER_CLASSES)}")
    if provider not in _instances:
        _instances[provider] = _ADAPTER_CLASSES[provider]()
    return _instances[provider]
