"""
Tests for holm_bonferroni_adjust / aggregate_significance (core/verifier.py),
added 2026-08-26 — the multiple-comparisons correction that only made sense
once ground_truth_significance_test existed as a real p-value-producing
check to correct.

Real problem this fixes: run enough independent statistical tests (e.g. one
per circuit in an angle sweep) and some will read "significant" at raw
p<0.05 just by chance, even if nothing real is going on. Holm-Bonferroni
controls that family-wise false-alarm rate.
"""
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.verifier as v


# ---------------------------------------------------------------------------
# holm_bonferroni_adjust — pure algorithm tests
# ---------------------------------------------------------------------------

def test_matches_hand_computed_textbook_example():
    """Classic worked example: p = [0.01, 0.02, 0.03, 0.04, 0.05], m=5.
    Step-down: multiplier 5,4,3,2,1 -> raw steps 0.05,0.08,0.09,0.08,0.05,
    then enforce non-decreasing (running max) -> 0.05,0.08,0.09,0.09,0.09."""
    result = v.holm_bonferroni_adjust([0.01, 0.02, 0.03, 0.04, 0.05])
    assert result == [0.05, 0.08, 0.09, 0.09, 0.09]


def test_empty_list_returns_empty():
    assert v.holm_bonferroni_adjust([]) == []


def test_single_pvalue_gets_multiplier_one_unchanged():
    """With only one test in the family, there's nothing to correct for --
    multiplier is m=1, so the adjusted value equals the raw value."""
    result = v.holm_bonferroni_adjust([0.03])
    assert result == [0.03]


def test_never_exceeds_one():
    result = v.holm_bonferroni_adjust([0.9, 0.9, 0.9])
    assert all(p <= 1.0 for p in result)


def test_adjusted_values_are_monotonically_non_decreasing_by_rank():
    """A required property of the step-down procedure: sorted by raw
    p-value ascending, the adjusted p-values must never decrease."""
    raw = [0.2, 0.001, 0.04, 0.01, 0.3]
    adjusted = v.holm_bonferroni_adjust(raw)
    by_rank = [adj for _, adj in sorted(zip(raw, adjusted), key=lambda x: x[0])]
    assert by_rank == sorted(by_rank)


def test_preserves_input_order_not_sorted_order():
    """Output must line up index-for-index with the input, not come back
    sorted by p-value."""
    raw = [0.5, 0.001, 0.2]
    adjusted = v.holm_bonferroni_adjust(raw)
    assert len(adjusted) == 3
    # the smallest raw p-value (index 1) must still be the smallest adjusted one
    assert adjusted[1] == min(adjusted)


def test_family_size_can_be_declared_larger_than_submitted_results():
    """Guards against exactly the bug flagged in review: 'm' must come
    from an explicitly declared family_size, not silently from how many
    p-values happen to get submitted -- otherwise a caller can drop the
    boring results and have the interesting one under-corrected."""
    raw = [0.01]
    adjusted_inferred = v.holm_bonferroni_adjust(raw)                    # m inferred = 1
    adjusted_declared = v.holm_bonferroni_adjust(raw, family_size=20)    # m declared = 20
    assert adjusted_inferred == [0.01]
    assert adjusted_declared == [0.2]  # 20 * 0.01, correctly harsher
    assert adjusted_declared[0] > adjusted_inferred[0]


def test_family_size_smaller_than_submitted_count_is_rejected():
    """Can never be legitimate -- you can't declare a family smaller than
    the number of tests you're actually submitting from it."""
    with pytest.raises(ValueError):
        v.holm_bonferroni_adjust([0.01, 0.02, 0.03], family_size=2)


def test_monte_carlo_family_wise_error_rate_is_controlled():
    """Validates the actual GUARANTEE, not just behavior on hand-built
    inputs: under the null (every p-value genuinely random/uniform, no
    real effects anywhere), the probability of getting AT LEAST ONE false
    "significant" result after correction must not exceed alpha, across
    many repeated trials. Seeded for reproducibility."""
    rng = random.Random(1234)
    trials = 5000
    m = 10
    alpha = 0.05
    false_positive_trials = 0
    for _ in range(trials):
        raw = [rng.random() for _ in range(m)]
        adjusted = v.holm_bonferroni_adjust(raw)
        if any(p < alpha for p in adjusted):
            false_positive_trials += 1
    observed_fwer = false_positive_trials / trials
    assert observed_fwer <= alpha * 1.25, (
        f"observed family-wise error rate {observed_fwer} exceeds the {alpha} guarantee "
        "by more than sampling noise should allow"
    )


# ---------------------------------------------------------------------------
# aggregate_significance — batch-level correction
# ---------------------------------------------------------------------------

def _fake_result(p_value, applicable=True):
    if not applicable:
        return {"ground_truth_significance_test": {"applicable": False}}
    return {"ground_truth_significance_test": {"applicable": True, "p_value": p_value}}


def test_catches_a_false_alarm_from_multiple_testing():
    """20 results all marginally 'significant' on their own (p just under
    0.05) -- after correcting for testing 20 claims at once, none of them
    should survive. This is the exact false-alarm scenario the correction
    exists to catch."""
    results = [_fake_result(0.04) for _ in range(20)]
    agg = v.aggregate_significance(results)
    assert agg["family_size"] == 20
    assert agg["significant_before_correction"] == 20
    assert agg["significant_after_correction"] == 0


def test_preserves_a_genuinely_strong_result_among_many_weak_ones():
    """One result with a tiny, real p-value sitting among many boring ones
    must survive correction -- Holm-Bonferroni shouldn't wash out a real
    effect, only the marginal ones."""
    results = [_fake_result(0.9) for _ in range(19)] + [_fake_result(0.0001)]
    agg = v.aggregate_significance(results)
    assert agg["family_size"] == 20
    assert agg["significant_after_correction"] == 1
    assert agg["results"][19]["significant_after_correction"] is True


def test_skips_non_applicable_results_without_breaking_indices():
    results = [_fake_result(0.01), _fake_result(None, applicable=False), _fake_result(0.02)]
    agg = v.aggregate_significance(results)
    assert agg["family_size"] == 2
    assert agg["results"][1]["applicable"] is False
    assert agg["results"][0]["applicable"] is True
    assert agg["results"][2]["applicable"] is True


def test_empty_batch_reports_nothing_to_correct():
    agg = v.aggregate_significance([])
    assert agg["family_size"] == 0
    assert "nothing to correct" in agg["verdict"].lower()


def test_aggregate_significance_uses_declared_family_size_not_submitted_count():
    """The p-hacking guard at the aggregate level: submitting only 1 of a
    declared 20-test family must correct as harshly as if all 20 were
    visible, not as if this were a lone test on its own."""
    results = [_fake_result(0.01)]
    agg_lone = v.aggregate_significance(results)
    agg_declared = v.aggregate_significance(results, family_size=20)
    assert agg_lone["results"][0]["holm_adjusted_p_value"] == 0.01
    assert agg_declared["results"][0]["holm_adjusted_p_value"] == 0.2
    assert agg_declared["family_size"] == 20
    assert agg_declared["submitted_count"] == 1
    assert "1 of 20" in agg_declared["verdict"]
