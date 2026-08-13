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
from core.memory import memory_summary

MIN_DATA_POINTS_FOR_RECOMMENDATION = 3


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
