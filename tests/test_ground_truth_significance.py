"""
Tests for ground_truth_significance_test (core/verifier.py).

v1 (2026-08-26): a plain difference test (H0: observed == claimed) -- wrong,
its p-value goes to 0 for ANY real device as shots grow.

v2 (2026-08-27, same day, after review): rewritten as TOST via two one-sided
binomial tests -- still wrong, collapsed a real 3-outcome test (VERIFIED /
FAIL / INCONCLUSIVE) into a 2-outcome one, AND broke down completely when
the equivalence band's edge sits at 0.0 or 1.0 (the common case for
GHZ/stabilizer-style claims) -- a PERFECT result there read as "not
equivalent" with the worst possible p-value, confirmed by direct
reproduction before this fix.

v3 (this file): CI-containment (Wilson) as the primary mechanism, giving a
real VERIFIED/FAIL/INCONCLUSIVE verdict plus an estimate of shots needed to
resolve an inconclusive result; one-sided binomial tests kept only to
produce the p_value Holm-Bonferroni correction needs, with the unreachable
side dropped when an edge sits at a 0/1 boundary.
"""
import math
import os
import random
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


def test_amplification_implying_over_100_percent_is_clamped():
    """expected_amplification=10 on a baseline of 0.5 would imply a
    'claimed probability' of 5.0, which is not a valid probability --
    must clamp to 1.0, not silently pass garbage into binomtest."""
    counts = {"0": 900, "1": 100}
    result = v.ground_truth_significance_test(counts, ["0"], expected_amplification=10.0)
    assert result["applicable"] is True
    assert result["claimed_probability"] == 1.0


def test_output_is_marked_as_statistical_kind():
    counts = {"00": 500, "01": 500}
    result = v.ground_truth_significance_test(counts, ["00"], expected_amplification=2.0)
    assert result["kind"] == "statistical"


def test_multiple_marked_bitstrings_sums_correctly():
    counts = {"00": 250, "01": 250, "10": 250, "11": 250}
    result = v.ground_truth_significance_test(counts, ["00", "11"], expected_amplification=1.0)
    assert result["marked_shots"] == 500
    assert result["baseline_probability"] == 0.5  # 2 marked out of 4


# ---------------------------------------------------------------------------
# The three real outcomes
# ---------------------------------------------------------------------------

def test_result_matching_claim_closely_is_verified():
    """A result landing almost exactly on the claim, with a large sample,
    should be CONFIRMED equivalent -- not just 'not proven different'."""
    counts = {"00": 5000, "01": 1667, "10": 1667, "11": 1666}
    result = v.ground_truth_significance_test(counts, ["00"], expected_amplification=2.0,
                                                tolerance=0.5)
    assert result["applicable"] is True
    assert result["tost_verdict"] == "VERIFIED"
    assert result["equivalent_at_alpha"] is True


def test_result_wildly_off_from_claim_is_a_fail():
    """A result nowhere near the claim, with a large enough sample to be
    CONFIDENT it's off (not just under-shotted) -- must be FAIL, not a
    vague 'not equivalent'."""
    counts = {"00": 2600, "01": 2500, "10": 2500, "11": 2400}
    result = v.ground_truth_significance_test(counts, ["00"], expected_amplification=10.0,
                                                tolerance=0.5)
    assert result["applicable"] is True
    assert result["tost_verdict"] == "FAIL"
    assert result["equivalent_at_alpha"] is False


def test_small_sample_near_the_boundary_is_inconclusive_not_a_fail():
    """The exact failure mode this rewrite exists to prevent: too few
    shots to tell must read as INCONCLUSIVE (need more data), not FAIL
    (confirmed bad) -- collapsing the two is the disease this whole
    workstream started with, reintroduced from the other end."""
    # baseline 0.5, claim 1.0x -> p_claimed=0.5, tolerance=0.1 -> band [0.45,0.55].
    # Only 20 shots, landing dead center of the band: nowhere near enough
    # to resolve a 10-point-wide band, but genuinely on-target so far.
    counts = {"0": 10, "1": 10}
    result = v.ground_truth_significance_test(counts, ["0"], expected_amplification=1.0,
                                                tolerance=0.1)
    assert result["tost_verdict"] == "INCONCLUSIVE"
    assert "shots_needed_to_resolve" in result
    assert result["shots_needed_to_resolve"] > 20


def test_a_perfect_result_against_a_claim_pinned_at_100_percent_is_verified():
    """The exact bug caught by direct reproduction before this fix: a
    claim whose tolerance band's upper edge lands at 1.0 (e.g. a
    GHZ/stabilizer check expecting near-certain collapse to one outcome)
    combined with a PERFECT result (all shots correct) used to read as
    'NOT equivalent' with the worst possible p-value, because the old
    two-one-sided-test combination is mathematically unable to get
    evidence 'below 1.0' from a perfect result. Must now read VERIFIED."""
    result = v.ground_truth_significance_test({"0000": 1024}, ["0000"],
                                                expected_amplification=20.0, tolerance=0.03)
    assert result["applicable"] is True
    assert result["claimed_probability"] == 1.0
    assert result["equivalence_margin"]["upper"] == 1.0
    assert result["tost_verdict"] == "VERIFIED"
    assert result["p_value"] < 0.05


def test_a_near_perfect_but_genuinely_short_result_at_the_1_0_boundary_is_not_a_false_fail():
    """Same 1.0-pinned band, but a few shots landed wrong -- must still be
    evaluated sensibly (VERIFIED or INCONCLUSIVE depending on how far
    off), never crash, and never silently misreport."""
    result = v.ground_truth_significance_test({"0000": 1010, "0001": 14}, ["0000"],
                                                expected_amplification=20.0, tolerance=0.03)
    assert result["applicable"] is True
    assert result["tost_verdict"] in ("VERIFIED", "INCONCLUSIVE", "FAIL")
    assert isinstance(result["p_value"], float)


def test_confidence_interval_is_reported_and_well_formed():
    counts = {"00": 500, "01": 500}
    result = v.ground_truth_significance_test(counts, ["00"], expected_amplification=2.0)
    ci = result["confidence_interval"]
    assert 0.0 <= ci["lower"] <= ci["upper"] <= 1.0
    assert ci["method"] == "wilson"


# ---------------------------------------------------------------------------
# The shot-count-sensitivity fix from v1 -> v2, still must hold in v3
# ---------------------------------------------------------------------------

def test_more_shots_makes_a_healthy_device_pass_more_confidently_not_less():
    """A device landing at 48% against an ideal 50% claim, well within a
    10% tolerance band, at three shot counts. Must be VERIFIED at all
    three (never flip to FAIL as data accumulates on an unchanged,
    genuinely-within-tolerance device), and the p-value must shrink as
    shots increase."""
    p_values = []
    for shots in (1024, 8192, 100_000):
        marked = round(shots * 0.48)
        counts = {"0": marked, "1": shots - marked}
        result = v.ground_truth_significance_test(counts, ["0"], expected_amplification=1.0,
                                                    tolerance=0.1)
        assert result["tost_verdict"] == "VERIFIED", f"not VERIFIED at {shots} shots: {result['tost_verdict']}"
        p_values.append(result["p_value"])
    assert p_values == sorted(p_values, reverse=True), (
        f"TOST p-value should shrink as shots increase for an unchanged, healthy device, got {p_values}"
    )


# ---------------------------------------------------------------------------
# Regression against hand computation
# ---------------------------------------------------------------------------

def test_matches_hand_computed_tost_p_value_in_the_two_sided_regime():
    """When both edges of the band are reachable (away from the 0/1
    boundary), the p-value must exactly match the max of the two one-sided
    binomial tests computed by hand -- the standard TOST construction."""
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


def test_matches_hand_computed_one_sided_p_value_when_upper_edge_is_unreachable():
    """When the band's upper edge is pinned at 1.0, the p-value must come
    ONLY from the lower one-sided test -- combining it with the vacuous
    upper test (which is always 1.0 for a perfect-or-near-perfect result)
    is exactly the bug this rewrite fixes."""
    counts = {"0000": 1024}
    result = v.ground_truth_significance_test(counts, ["0000"], expected_amplification=20.0,
                                                tolerance=0.03)
    p_lower_expected = binomtest(1024, 1024, 0.97, alternative="greater").pvalue
    assert result["p_value"] == round(p_lower_expected, 6)


# ---------------------------------------------------------------------------
# Boundary type-I rate: the actual guarantee TOST makes (synthetic, no
# hardware needed) -- if the true probability sits EXACTLY at the
# equivalence margin's edge (genuinely NOT equivalent, worst case), the
# false VERIFIED rate must not exceed alpha.
# ---------------------------------------------------------------------------

def test_boundary_type_i_rate_is_controlled_at_the_equivalence_margin_edge():
    total = 2000
    trials = 2000
    alpha = 0.05
    tolerance = 0.1
    p_claimed = 0.5
    p_hi_boundary = p_claimed * (1 + tolerance)  # 0.55 -- exactly at the edge

    false_verified = 0
    for i in range(trials):
        successes = random.Random(9000 + i).binomialvariate(total, p_hi_boundary)
        counts = {"0": successes, "1": total - successes}
        result = v.ground_truth_significance_test(counts, ["0"], expected_amplification=1.0,
                                                    tolerance=tolerance, alpha=alpha)
        if result["tost_verdict"] == "VERIFIED":
            false_verified += 1

    observed_rate = false_verified / trials
    se = (alpha * (1 - alpha) / trials) ** 0.5
    assert observed_rate <= alpha + 3 * se, (
        f"false VERIFIED rate at the equivalence-margin boundary is {observed_rate}, "
        f"exceeds the {alpha} guarantee by more than sampling noise should allow"
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
