"""
Intelligence/recommendation layer — the third piece named in the original
plan alongside Memory and Postmortem, never built until now, held back
(correctly) until Memory had real data to draw from.

Deliberately bounded scope for this first version: one real, honest,
data-driven recommendation — what amplification_tolerance actually makes
sense for a given provider/device, based on how accurate this tool's
predictions have really been for it, not a guessed default applied
everywhere. Grows more useful as more real data accumulates through
core/memory.py; makes no claim beyond what the real sample size supports.

Deliberately NOT built yet, a real scope boundary: automatic postmortem
explanation of *why* a specific prediction was wrong (would need failure-
mode classification this project doesn't have data for yet), and any
broader "what should I try next" recommendation beyond tolerance.
"""
import math

from core.memory import memory_summary

MIN_DATA_POINTS_FOR_RECOMMENDATION = 3

# Buhlmann-style credibility smoothing constant for estimate_tolerance_band
# below -- picked so trust in real history grows smoothly instead of
# recommend_tolerance's hard cliff at MIN_DATA_POINTS_FOR_RECOMMENDATION.
# At n=3 (the old cliff point) this gives Z=0.33 (partial trust, not full);
# at n=12, Z=0.67; at n=24, Z=0.8 -- approaches full trust as real data
# accumulates, never jumps.
CREDIBILITY_K = MIN_DATA_POINTS_FOR_RECOMMENDATION * 2


def recommend_tolerance(provider: str, target_device: str, default: float = 0.5) -> dict:
    """
    Recommends an amplification_tolerance for verify_experiment/
    ionq_submit_job based on this tool's REAL historical prediction
    accuracy for this specific provider/device — not a guessed default
    applied everywhere regardless of how trustworthy predictions have
    actually been.

    Honest about its own limits: with fewer than
    MIN_DATA_POINTS_FOR_RECOMMENDATION real prediction-vs-reality pairs
    recorded, this returns the plain default rather than pretending a
    tiny sample justifies a confident recommendation.
    """
    summary = memory_summary(provider)
    key = f"{provider}/{target_device}"
    device_data = summary.get("by_provider_device", {}).get(key)

    if not device_data or device_data["n"] < MIN_DATA_POINTS_FOR_RECOMMENDATION:
        return {
            "recommended_tolerance": default,
            "confidence": "default — not enough real data yet",
            "n_real_data_points": device_data["n"] if device_data else 0,
            "note": (f"Fewer than {MIN_DATA_POINTS_FOR_RECOMMENDATION} real prediction-vs-reality "
                     f"pairs recorded for {key}. Using the standard default until more real "
                     "data accumulates — recommending anything more specific from this little "
                     "data would be overclaiming."),
        }

    mean_error = device_data["mean_relative_error"]
    # Recommend comfortably above the observed error, not the observed
    # error itself -- a tolerance set exactly at past average error would
    # still reject about half of future predictions with typical variance.
    recommended = round(min(1.0, mean_error * 1.5 + 0.1), 3)

    return {
        "recommended_tolerance": recommended,
        "confidence": f"based on {device_data['n']} real prediction-vs-reality data point(s)",
        "n_real_data_points": device_data["n"],
        "observed_mean_relative_error": mean_error,
        "note": (f"Real predictions for {key} have been off by ~{mean_error * 100:.1f}% on "
                 "average so far. Recommending a tolerance with real margin above that "
                 "observed error, not the bare observed error itself — small sample sizes "
                 "should still be read with real caution."),
    }


def _calibration_drift_proxy(target_device: str, n_two_qubit_gates) -> float:
    """
    Added 2026-09-26. A real, computed systematic-uncertainty estimate for
    a device with no real prediction-vs-reality history yet -- IBM's
    situation until providers/ibm.py:submit_job's new self-check
    accumulates data (see estimate_tolerance_band below). Deliberately
    does NOT attempt to reconstruct a full noise model from a historical
    calibration snapshot (a real, much bigger engineering task, out of
    scope for this pass) -- instead reuses data and a formula this project
    already has: how much has this device's own average two-qubit gate
    error rate actually varied over the last week
    (providers/ibm.py:device_history), propagated through the exact same
    product-of-gate-errors pattern adapters/ibm.py:simulate_hardware_aware
    already uses for estimated_fidelity ((1 - avg_cx_error) ** n_cx).

    Returns None (never a fabricated number) when there isn't enough real
    calibration history to compute a real spread from, or the circuit's
    two-qubit gate count isn't known.
    """
    if not n_two_qubit_gates:
        return None
    try:
        from providers.ibm import device_history
    except ImportError:
        return None
    try:
        history = device_history(target_device, days=7)
    except Exception:
        return None
    errors = [s["avg_cx_error"] for s in history.get("snapshots", []) if s.get("avg_cx_error") is not None]
    if len(errors) < 2:
        return None
    mean_err = sum(errors) / len(errors)
    variance = sum((e - mean_err) ** 2 for e in errors) / (len(errors) - 1)
    std_err = math.sqrt(variance)
    fidelity_at_mean = (1 - mean_err) ** n_two_qubit_gates
    fidelity_at_mean_plus_std = (1 - min(1.0, mean_err + std_err)) ** n_two_qubit_gates
    return round(abs(fidelity_at_mean - fidelity_at_mean_plus_std), 4)


def estimate_tolerance_band(provider: str, target_device: str, hw_result: dict,
                             marked_bitstrings: list, default: float = 0.5,
                             alpha: float = 0.05) -> dict:
    """
    Added 2026-09-26, the job-einstein redesign (see project journal for
    the Gemini/Opus-5.5/ChatGPT background). Replaces recommend_tolerance's
    single guessed percentage with a decomposed estimate built from two
    genuinely different sources of error, instead of one blended guess:

    - statistical: sampling noise in hw_result's own counts. Note this is
      the noise-aware SIMULATION's shot count, not real hardware's --
      verify() never touches real hardware (hardware_aware_simulation
      always dispatches to a simulator on both providers; see that
      function and ground_truth_check's docstring). Computed via
      core.verifier.wilson_score_interval, the exact same math
      ground_truth_significance_test already uses, so this can never
      silently drift from that check's own numbers.
    - systematic: how far off this tool's predictions have historically
      been for this provider/device (core.memory.memory_summary), blended
      toward `default` with smooth Buhlmann-style credibility weighting
      (CREDIBILITY_K above) instead of recommend_tolerance's hard cutoff
      at MIN_DATA_POINTS_FOR_RECOMMENDATION. When there isn't enough real
      history yet, falls back to _calibration_drift_proxy -- a real,
      computed number from calibration data that already exists, not a
      static guess. Falls back to `default` itself only when neither real
      history nor real calibration data is available.

    Combined via quadrature into one number. Deliberately NOT bias-
    corrected/asymmetric this pass (real hardware likely underperforms
    predictions in one direction, per the Einstein consultation) --
    that needs real prediction-vs-reality data to know which direction to
    skew, which is exactly what this pass starts collecting for IBM. A
    documented simplification, not an oversight.

    Shadow-mode only for now -- see core.memory.record_tolerance_v2_comparison
    and core.verifier.verify(), which logs this alongside the existing
    flat-tolerance check without letting it change the actual verdict.
    """
    hw_counts = (hw_result or {}).get("counts")
    if not hw_counts:
        return {"applicable": False, "note": "No counts available to estimate a tolerance band from."}

    from core.verifier import wilson_score_interval

    total = sum(hw_counts.values())
    marked = set(marked_bitstrings or [])
    n_qubits = len(next(iter(hw_counts.keys())))
    marked_shots = sum(c for b, c in hw_counts.items() if b in marked)
    baseline_p = len(marked) / (2 ** n_qubits) if n_qubits else 0
    if total == 0 or baseline_p <= 0:
        return {"applicable": False, "note": "Zero shots or no marked bitstrings — nothing to estimate."}

    ci_lo, ci_hi = wilson_score_interval(marked_shots, total, alpha)
    phat = marked_shots / total
    observed_amp = phat / baseline_p
    amp_lo, amp_hi = ci_lo / baseline_p, ci_hi / baseline_p
    statistical = ((amp_hi - amp_lo) / (2 * observed_amp)) if observed_amp > 0 else min(1.0, amp_hi)

    summary = memory_summary(provider)
    key = f"{provider}/{target_device}"
    device_data = summary.get("by_provider_device", {}).get(key)
    n_real = device_data["n"] if device_data else 0

    if device_data and device_data.get("mean_relative_error") is not None:
        credibility = n_real / (n_real + CREDIBILITY_K)
        historical = min(1.0, device_data["mean_relative_error"] * 1.5 + 0.1)
        systematic = credibility * historical + (1 - credibility) * default
        systematic_source = f"historical (blended, n={n_real}, credibility={round(credibility, 2)})"
    else:
        n_two_qubit_gates = hw_result.get("n_two_qubit_gates")
        drift_proxy = _calibration_drift_proxy(target_device, n_two_qubit_gates)
        if drift_proxy is not None:
            systematic = drift_proxy
            systematic_source = "calibration_drift_proxy"
        else:
            systematic = default
            systematic_source = "default (no history, no calibration data)"

    combined = min(1.0, math.sqrt(statistical ** 2 + systematic ** 2))
    return {
        "applicable": True,
        "tolerance": round(combined, 4),
        "statistical": round(statistical, 4),
        "systematic": round(systematic, 4),
        "systematic_source": systematic_source,
        "n_real_data_points": n_real,
        "note": ("Statistical half is real sampling noise from this run's own noise-aware "
                 "simulation shots. Systematic half is either this tool's real historical "
                 "accuracy for this provider/device (blended smoothly by sample size), or, "
                 "with too little history, a real calibration-drift-based estimate — never a "
                 "silent guess. Shadow-mode only: does not change the actual verdict."),
    }
