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
import sys

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
