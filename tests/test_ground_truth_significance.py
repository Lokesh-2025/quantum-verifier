"""
Tests for ground_truth_significance_test (core/verifier.py), added
2026-08-26 — the real statistical hypothesis test that was missing before
any multiple-comparisons correction work could make sense. The old
ground_truth_check only ever compared against a fixed +/-50% tolerance
band, never produced a real probability. This uses scipy's exact
one-sample binomial test instead.

Pure math tests here (scipy's binomtest itself is well-established and
doesn't need re-proving) — what actually needs testing is the wrapper
logic I wrote around it: baseline probability, clamping, edge cases.
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


def test_matches_scipy_binomtest_directly_on_a_known_case():
    """Regression/sanity: our wrapper's p-value must exactly match calling
    scipy's binomtest ourselves with the same real numbers, by hand."""
    counts = {"00": 450, "01": 50, "10": 30, "11": 20}
    result = v.ground_truth_significance_test(counts, ["00"], expected_amplification=1.5)
    total = 550
    baseline_p = 1 / 4  # 1 marked bitstring out of 2^2 = 4 possibilities
    p_claimed = min(1.0, baseline_p * 1.5)
    expected = binomtest(450, total, p_claimed, alternative="two-sided").pvalue
    assert result["p_value"] == round(expected, 6)


def test_result_matching_claim_exactly_gives_a_large_p_value():
    """A result landing almost exactly where the claim predicts should be
    'boring' -- a high p-value, not flagged as unusual."""
    # baseline 1/4, claim 2x -> claimed_probability = 0.5. Land almost
    # exactly on 0.5 with a large sample.
    counts = {"00": 5000, "01": 1667, "10": 1667, "11": 1666}
    result = v.ground_truth_significance_test(counts, ["00"], expected_amplification=2.0)
    assert result["applicable"] is True
    assert result["p_value"] > 0.05
    assert result["significant_at_0.05"] is False


def test_result_wildly_off_from_claim_gives_a_tiny_p_value():
    """A result nowhere near the claim, with a large sample size (so it's
    not just noise), should produce a genuinely tiny p-value."""
    # Claim: 10x amplification (claimed_probability = min(1, 0.25*10) = 1.0).
    # Real result: barely above baseline. With 10,000 shots this is not
    # remotely consistent with "should be ~100% marked".
    counts = {"00": 2600, "01": 2500, "10": 2500, "11": 2400}
    result = v.ground_truth_significance_test(counts, ["00"], expected_amplification=10.0)
    assert result["applicable"] is True
    assert result["p_value"] < 0.001
    assert result["significant_at_0.05"] is True


def test_amplification_implying_over_100_percent_is_clamped():
    """expected_amplification=10 on a baseline of 0.5 would imply a
    'claimed probability' of 5.0, which is not a valid probability --
    must clamp to 1.0, not silently pass garbage into binomtest."""
    counts = {"0": 900, "1": 100}
    result = v.ground_truth_significance_test(counts, ["0"], expected_amplification=10.0)
    assert result["applicable"] is True
    assert result["claimed_probability"] == 1.0


def test_output_is_marked_as_statistical_kind():
    """The 'kind' field matters for the future triage/family-grouping work
    -- this check must self-identify as belonging in the real-statistics
    group, not silently be lumped in with heuristics."""
    counts = {"00": 500, "01": 500}
    result = v.ground_truth_significance_test(counts, ["00"], expected_amplification=2.0)
    assert result["kind"] == "statistical"


def test_multiple_marked_bitstrings_sums_correctly():
    counts = {"00": 250, "01": 250, "10": 250, "11": 250}
    result = v.ground_truth_significance_test(counts, ["00", "11"], expected_amplification=1.0)
    assert result["marked_shots"] == 500
    assert result["baseline_probability"] == 0.5  # 2 marked out of 4


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
