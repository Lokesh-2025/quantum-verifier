"""
Cross-provider cost/quality router.

IBM's and IonQ's pricing and quality signals are genuinely different in
kind, not just in number — IBM's free tier is a QPU-minutes quota, IonQ's
is a dollar cost; IBM's fidelity signal here is a live-calibration product-
of-gate-errors ESTIMATE, IonQ's is a real noisy SIMULATION compared against
the ideal case. Collapsing those into one fake unified score would hide
exactly the kind of asymmetry this project has always stated explicitly
elsewhere (see hardware_aware_simulation's docstring). So this reports both
axes side by side, in their real units, and recommends from that — it does
not force a single number.
"""
from qiskit import QuantumCircuit

from core.verifier import _parse, ideal_simulation, hardware_aware_simulation
from providers.ibm import estimate_runtime as _ibm_estimate_runtime
from providers.ionq import estimate_ionq_cost as _ionq_estimate_cost


def _ionq_fidelity_proxy(qasm_string: str, target_device: str, shots: int) -> dict:
    """
    IonQ's hardware-aware path is a full noisy simulation, not an estimate —
    so a genuine fidelity proxy is available: 1 - total variation distance
    between the noiseless-ideal output and the noisy-predicted output.
    Bounded [0, 1], directly comparable in spirit to IBM's estimated_fidelity
    even though the two are computed differently (documented above).
    """
    circuit = _parse(qasm_string)
    ideal = ideal_simulation(circuit, shots)
    noisy = hardware_aware_simulation(circuit, "ionq", target_device, shots)
    if "error" in noisy:
        return {"error": noisy["error"]}
    ideal_counts, noisy_counts = ideal["counts"], noisy.get("counts")
    if not noisy_counts:
        return {"error": "No counts returned from IonQ hardware-aware simulation."}
    ideal_total = sum(ideal_counts.values())
    noisy_total = sum(noisy_counts.values())
    all_keys = set(ideal_counts) | set(noisy_counts)
    tvd = 0.5 * sum(
        abs(ideal_counts.get(k, 0) / ideal_total - noisy_counts.get(k, 0) / noisy_total)
        for k in all_keys
    )
    return {
        "fidelity_proxy": round(1 - tvd, 4),
        "noise_model_used": noisy.get("noise_model_used"),
        "gate_synthesis_check": noisy.get("gate_synthesis_check"),
    }


def find_optimal_backend(
    qasm_string: str,
    ibm_device: str = "",
    ionq_device: str = "forte-1",
    shots: int = 4096,
) -> dict:
    """
    Side-by-side comparison of running the same circuit on IBM vs IonQ:
    real cost signal (IBM QPU-minutes quota vs IonQ dollar range) and real
    quality signal (IBM live-calibration fidelity estimate vs IonQ noisy-
    simulation fidelity proxy) for each, plus a recommendation.

    Args:
        qasm_string : OpenQASM 2.0 circuit string
        ibm_device  : IBM backend name (e.g. "ibm_fez"); skipped if blank
        ionq_device : IonQ target (e.g. "forte-1"); skipped if blank
        shots       : shots for both cost and simulation estimates

    Either provider can be individually unreachable (missing credentials,
    device not found) — that provider's entry reports its own error and the
    other still gets compared.
    """
    result = {"ibm": None, "ionq": None}

    if ibm_device:
        runtime_est = _ibm_estimate_runtime(qasm_string, ibm_device, shots)
        if "error" in runtime_est:
            result["ibm"] = {"error": runtime_est["error"]}
        else:
            circuit = _parse(qasm_string)
            hw = hardware_aware_simulation(circuit, "ibm", ibm_device, shots)
            result["ibm"] = {
                "device": ibm_device,
                "cost": {"unit": "QPU minutes (free-tier quota)", "estimated_minutes": runtime_est["total_estimate_mins"]},
                "quality": {"estimated_fidelity": hw.get("estimated_fidelity"), "source": hw.get("simulation_type")},
                "n_two_qubit_gates": hw.get("n_two_qubit_gates"),
            }

    if ionq_device:
        cost_est = _ionq_estimate_cost([qasm_string], shots)
        if "error" in cost_est:
            result["ionq"] = {"error": cost_est["error"]}
        else:
            fidelity = _ionq_fidelity_proxy(qasm_string, ionq_device, shots)
            result["ionq"] = {
                "device": ionq_device,
                "cost": {
                    "unit": "USD",
                    "estimated_usd_low": cost_est.get("estimated_total_usd_low"),
                    "estimated_usd_high": cost_est.get("estimated_total_usd_high"),
                },
                "quality": (
                    {"error": fidelity["error"]} if "error" in fidelity else
                    {"fidelity_proxy": fidelity["fidelity_proxy"], "source": "noisy simulation vs ideal, real noise model"}
                ),
            }

    available = {k: v for k, v in result.items() if v and "error" not in v}
    if not available:
        result["recommendation"] = "Neither provider produced a usable estimate — check device names and credentials."
    elif len(available) == 1:
        provider = next(iter(available))
        result["recommendation"] = f"Only {provider} produced a usable estimate for this circuit/device pair."
    else:
        ibm_fid = result["ibm"]["quality"].get("estimated_fidelity")
        ionq_fid = result["ionq"]["quality"].get("fidelity_proxy") if "error" not in result["ionq"]["quality"] else None
        parts = [
            f"IBM ({ibm_device}): ~{result['ibm']['cost']['estimated_minutes']} free-tier QPU minutes, "
            f"estimated fidelity {ibm_fid}." if ibm_fid is not None else f"IBM ({ibm_device}): cost available, quality unavailable.",
            f"IonQ ({ionq_device}): ${result['ionq']['cost']['estimated_usd_low']}-"
            f"${result['ionq']['cost']['estimated_usd_high']}, "
            f"fidelity proxy {ionq_fid}." if ionq_fid is not None else f"IonQ ({ionq_device}): cost available, quality unavailable.",
        ]
        result["recommendation"] = (
            " ".join(parts) + " Cost units differ (free quota minutes vs real dollars) and quality signals are "
            "computed differently (calibration estimate vs noisy simulation) — reported side by side rather than "
            "collapsed into one score, since that asymmetry is real, not a formatting choice."
        )

    return result
