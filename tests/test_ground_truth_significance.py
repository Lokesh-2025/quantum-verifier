"""
Tests for ground_truth_significance_test (core/verifier.py).

Added 2026-08-26 as a plain difference test (H0: observed == claimed).
REWRITTEN 2026-08-27 after external review caught a real correctness bug:
a difference test's p-value goes to 0 with probability 1 as shots grow,
for ANY real (noisy) device, regardless of whether it's actually healthy —
backwards for a safety gate that should get MORE confident with more data,
not less. Rewritten as TOST (two one-sided tests): H0 = "not equivalent to
the claim within tolerance". Small p_value now means strong evidence FOR
equivalence — the opposite direction from the old version.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scipy.stats import binomtest

import core.verifier as v


def test_not_applicable_when_no_counts():
    result = v.ground_truth_significance_test(None, ["00"], 2.0)
    assert result["applicable"] is False


def test_not_applicable_when_zero_shots():
    result = v.ground_truth_significance_test({"00": 0, "01": 0}, ["00"], 2.0)
    assert result["applicable"] is False


def test_not_applicable_when_no_marked_bitstrings():
    result = v.ground_truth_significance_test({"00": 100}, [], 2.0)
    assert result["applicable"] is False


def test_not_applicable_when_equivalence_band_is_degenerate():
    """expected_amplification=0 -> claimed_probability=0 -> the tolerance
    band collapses to a single point -- nothing meaningful to test."""
    result = v.ground_truth_significance_test({"0": 500, "1": 500}, ["0"],
                                                expected_amplification=0.0)
    assert result["applicable"] is False


def test_matches_hand_computed_tost_directly_on_a_known_case():
    """Regression/sanity: our TOST wrapper's p-value must exactly match
    computing the two one-sided binomial tests ourselves, by hand, and
    taking the max — the standard TOST construction."""
    counts = {"00": 450, "01": 50, "10": 30, "11": 20}
    result = v.ground_truth_significance_test(counts, ["00"], expected_amplification=1.5,
                                                tolerance=0.2)
    total = 550
    baseline_p = 1 / 4
    p_claimed = min(1.0, baseline_p * 1.5)
    p_lo = p_claimed * (1 - 0.2)
    p_hi = p_claimed * (1 + 0.2)
    p_upper = binomtest(450, total, p_hi, alternative="less").pvalue
    p_lower = binomtest(450, total, p_lo, alternative="greater").pvalue
    expected = max(p_upper, p_lower)
    assert result["p_value"] == round(expected, 6)


def test_result_matching_claim_closely_gives_a_small_p_value_and_passes():
    """A result landing almost exactly on the claim, with a large sample,
    should be CONFIDENTLY declared equivalent -- small TOST p-value,
    equivalent_at_alpha True."""
    counts = {"00": 5000, "01": 1667, "10": 1667, "11": 1666}
    result = v.ground_truth_significance_test(counts, ["00"], expected_amplification=2.0,
                                                tolerance=0.5)
    assert result["applicable"] is True
    assert result["p_value"] < 0.05
    assert result["equivalent_at_alpha"] is True


def test_result_wildly_off_from_claim_gives_a_large_p_value_and_fails():
    """A result nowhere near the claim (even clamped to the claim's max
    possible probability) must NOT be confirmed equivalent -- large TOST
    p-value, equivalent_at_alpha False."""
    counts = {"00": 2600, "01": 2500, "10": 2500, "11": 2400}
    result = v.ground_truth_significance_test(counts, ["00"], expected_amplification=10.0,
                                                tolerance=0.5)
    assert result["applicable"] is True
    assert result["p_value"] > 0.05
    assert result["equivalent_at_alpha"] is False


def test_amplification_implying_over_100_percent_is_clamped():
    """expected_amplification=10 on a baseline of 0.5 would imply a
    'claimed probability' of 5.0, which is not a valid probability --
    must clamp to 1.0, not silently pass garbage into binomtest."""
    counts = {"0": 900, "1": 100}
    result = v.ground_truth_significance_test(counts, ["0"], expected_amplification=10.0)
    assert result["applicable"] is True
    assert result["claimed_probability"] == 1.0


def test_output_is_marked_as_statistical_kind():
    """The 'kind' field matters for the taxonomy/family-grouping work --
    this check must self-identify as belonging in the real-statistics
    group, not silently be lumped in with heuristics."""
    counts = {"00": 500, "01": 500}
    result = v.ground_truth_significance_test(counts, ["00"], expected_amplification=2.0)
    assert result["kind"] == "statistical"


def test_multiple_marked_bitstrings_sums_correctly():
    counts = {"00": 250, "01": 250, "10": 250, "11": 250}
    result = v.ground_truth_significance_test(counts, ["00", "11"], expected_amplification=1.0)
    assert result["marked_shots"] == 500
    assert result["baseline_probability"] == 0.5  # 2 marked out of 4


def test_more_shots_makes_a_healthy_device_pass_more_confidently_not_less():
    """The exact bug this rewrite fixes, demonstrated directly: the old
    difference-test version's p-value -> 0 as shots -> infinity for ANY
    real device, flipping a healthy device's verdict from pass to
    hard-fail purely from sample size. Here: a device landing at 48%
    against an ideal 50% claim (well within a 10% tolerance band) at three
    shot counts. The TOST p-value must SHRINK as shots increase, and the
    verdict must never flip to 'not equivalent' as data accumulates on an
    unchanged, genuinely-within-tolerance device."""
    p_values = []
    for shots in (1024, 8192, 100_000):
        marked = round(shots * 0.48)
        counts = {"0": marked, "1": shots - marked}
        result = v.ground_truth_significance_test(counts, ["0"], expected_amplification=1.0,
                                                    tolerance=0.1)
        assert result["equivalent_at_alpha"] is True, f"flipped to not-equivalent at {shots} shots"
        p_values.append(result["p_value"])
    assert p_values == sorted(p_values, reverse=True), (
        "TOST p-value should shrink as shots increase for an unchanged, healthy device, "
        f"got {p_values}"
    )


# ---------------------------------------------------------------------------
# Integration: verify() includes this as an informational field only
# ---------------------------------------------------------------------------

BELL = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q[0] -> c[0];
measure q[1] -> c[1];
""".strip()


def test_verify_includes_significance_test_without_changing_existing_behavior(monkeypatch):
    """The new field must appear alongside the old check, and must NOT
    change verify()'s existing block/pass behavior -- it's informational
    only, for now, on purpose."""
    def _fake_hw(circuit, provider, target_device, shots):
        return {"counts": {"00": 2048, "11": 2048}, "total_shots": 4096}

    monkeypatch.setattr(v, "hardware_aware_simulation", _fake_hw)

    result = v.verify(BELL, provider="ionq", target_device="simulator", shots=4096,
                       expected_marked_bitstrings=["00", "11"], expected_amplification=2.0)
    assert "ground_truth_significance_test" in result
    assert result["ground_truth_significance_test"]["applicable"] is True
    # old check still drives the actual verdict, unchanged
    assert result["verdict"] == "GO"
