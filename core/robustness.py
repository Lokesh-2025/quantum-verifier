"""
Robustness-aware circuit selection.

Generalizes a real pattern this project hit: a circuit (E1's degree3
variant) was tuned to maximize performance in a perfect, noiseless
simulation (18.8x ideal), and that tuning process had nothing selecting
for how well the winning point survives real noise. It didn't -- two
independent real-noise checks both landed around 7x, while a *different*,
lower-scoring-on-paper candidate (13.2x ideal) consistently landed around
12x on real noise across three independent runs, including one it was
never tuned or selected on.

The lesson generalizes: picking a circuit/parameter choice by its ideal
simulation score alone can select a fragile point that looks great on
paper and falls apart on real hardware. This module makes that mistake
harder to repeat by requiring the caller to score candidates against real
noise and validate the winner on a held-out run, not just trust the best
ideal number.
"""
import numpy as np

from core.verifier import hardware_aware_simulation, ground_truth_check, _parse


def _observed_amplification(qasm_string: str, provider: str, target_device: str,
                             marked_bitstrings: list, shots: int):
    circuit = _parse(qasm_string)
    hw = hardware_aware_simulation(circuit, provider, target_device, shots)
    if "error" in hw:
        return None
    gt = ground_truth_check(hw.get("counts"), marked_bitstrings, expected_amplification=1.0, tolerance=100)
    if not gt.get("applicable"):
        return None
    return gt.get("observed_amplification")


def find_robust_circuit(
    candidate_qasm_circuits: list,
    provider: str,
    target_device: str,
    marked_bitstrings: list,
    shots: int = 2048,
    n_scoring_runs: int = 2,
    variance_penalty: float = 1.0,
) -> dict:
    """
    Given several candidate circuits for the same problem (e.g. the same
    circuit with different tunable parameters), scores each against REAL
    target-device noise -- n_scoring_runs independent runs per candidate,
    scored as mean amplification minus variance_penalty * standard
    deviation, so a lucky-but-fragile candidate loses to a consistently
    solid one. The winner is then validated on ONE MORE fresh run it was
    never scored on -- a real train/test split, not just picking whichever
    number looked best once.

    Args:
        candidate_qasm_circuits : list of OpenQASM 2.0 circuit strings,
                                   different candidates for the same problem
        provider                : "ibm" or "ionq"
        target_device            : real target device name (noise model
                                    used for scoring must match what the
                                    circuit will actually run on)
        marked_bitstrings        : target bitstrings defining "success" for
                                    this problem (same convention as
                                    verify_experiment/get_amplification)
        shots                    : shots per run
        n_scoring_runs           : independent noisy runs per candidate
                                    used for scoring (default 2)
        variance_penalty         : how much to penalize inconsistency
                                    across scoring runs (default 1.0 --
                                    one full standard deviation)

    Returns the winning circuit's index, its scoring/validation data for
    every candidate (so the full comparison is visible, not just the
    winner), and an honest warning if validation contradicts the scoring.
    """
    if len(candidate_qasm_circuits) < 2:
        return {"error": "Need at least 2 candidates to compare — this tool is for choosing between options, not evaluating one."}

    results = []
    for i, qasm in enumerate(candidate_qasm_circuits):
        runs = [_observed_amplification(qasm, provider, target_device, marked_bitstrings, shots)
                for _ in range(n_scoring_runs)]
        runs = [r for r in runs if r is not None]
        if not runs:
            results.append({"candidate_index": i, "error": "all scoring runs failed"})
            continue
        mean_amp = float(np.mean(runs))
        std_amp = float(np.std(runs)) if len(runs) > 1 else 0.0
        results.append({
            "candidate_index": i, "scoring_runs": [round(r, 3) for r in runs],
            "mean_amplification": round(mean_amp, 3), "std_amplification": round(std_amp, 3),
            "robustness_score": round(mean_amp - variance_penalty * std_amp, 3),
        })

    scored = [r for r in results if "robustness_score" in r]
    if not scored:
        return {"error": "All candidates failed to score against real noise.", "results": results}

    winner = max(scored, key=lambda r: r["robustness_score"])
    winner_index = winner["candidate_index"]
    validation = _observed_amplification(
        candidate_qasm_circuits[winner_index], provider, target_device, marked_bitstrings, shots
    )

    warning = None
    if validation is not None and winner["scoring_runs"]:
        scoring_mean = winner["mean_amplification"]
        if scoring_mean and abs(validation - scoring_mean) / max(scoring_mean, 1e-9) > 0.5:
            warning = (f"Validation run ({validation:.2f}) differs from scoring mean "
                       f"({scoring_mean:.2f}) by more than 50% — the winner may not be as "
                       "robust as the scoring runs suggested. Consider more scoring runs "
                       "before trusting this result.")

    return {
        "winner_index": winner_index,
        "winner_robustness_score": winner["robustness_score"],
        "validation_run": round(validation, 3) if validation is not None else None,
        "validation_warning": warning,
        "all_candidates": results,
        "note": "Compare winner_robustness_score's candidates against each candidate's OWN "
                "ideal/noiseless score separately if picking by real-noise performance alone "
                "surprises you — a lower ideal score winning is expected behavior, not a bug.",
    }
