"""
Tests for record_shadow_mode_comparison / shadow_mode_disagreement_log
(core/memory.py), added 2026-08-27 per external review's item 4 — logging,
for free, every time the old tolerance-band check (ground_truth_check) and
the new statistical equivalence test (ground_truth_significance_test)
agree or disagree. Doesn't need a real hardware result to be useful.

Schema rewritten same day (second review pass): logs raw counts and full
statistical detail (not just an agree/disagree boolean) — so the verdict
can be recomputed later under a different alpha, tolerance, or CI method,
which a boolean-only log makes impossible.

Uses an isolated temp db (monkeypatched _DB_PATH) — never the real
experiment_memory.db.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.memory as memory


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_experiment_memory.db")
    monkeypatch.setattr(memory, "_DB_PATH", db_path)
    return db_path


def _old(within_tolerance):
    return {"applicable": True, "within_tolerance": within_tolerance}


def _new(equivalent, p_value=0.01, tost_verdict=None, total_shots=1000, marked_shots=500,
         claimed_probability=0.5, ci=(0.47, 0.53)):
    return {
        "applicable": True,
        "equivalent_at_alpha": equivalent,
        "p_value": p_value,
        "tost_verdict": tost_verdict or ("VERIFIED" if equivalent else "FAIL"),
        "total_shots": total_shots,
        "marked_shots": marked_shots,
        "claimed_probability": claimed_probability,
        "equivalence_margin": {"lower": claimed_probability - 0.05, "upper": claimed_probability + 0.05},
        "alpha": 0.05,
        "confidence_interval": {"lower": ci[0], "upper": ci[1], "method": "wilson"},
    }


def test_no_comparisons_logged_reports_zero_not_a_crash(temp_db):
    result = memory.shadow_mode_disagreement_log()
    assert result["total_comparisons_logged"] == 0
    assert result["disagreement_count"] == 0


def test_agreement_is_logged_but_not_counted_as_a_disagreement(temp_db):
    memory.record_shadow_mode_comparison("ionq", "forte-1", "OPENQASM 2.0;",
                                          _old(True), _new(True))
    result = memory.shadow_mode_disagreement_log()
    assert result["total_comparisons_logged"] == 1
    assert result["disagreement_count"] == 0


def test_disagreement_is_logged_with_full_raw_detail_not_just_a_flag(temp_db):
    """The whole point of the schema rewrite: enough is logged to
    recompute the verdict later, not just replay a boolean."""
    memory.record_shadow_mode_comparison("ionq", "forte-1", "OPENQASM 2.0;",
                                          _old(True), _new(False, p_value=0.4, tost_verdict="INCONCLUSIVE",
                                                            total_shots=2000, marked_shots=1010),
                                          old_check_tolerance=0.5)
    result = memory.shadow_mode_disagreement_log()
    assert result["total_comparisons_logged"] == 1
    assert result["disagreement_count"] == 1
    row = result["disagreements"][0]
    assert row["old_check_said_within_tolerance"] is True
    assert row["new_check_tost_verdict"] == "INCONCLUSIVE"
    assert row["p_value"] == 0.4
    assert row["total_shots"] == 2000
    assert row["marked_shots"] == 1010
    assert row["confidence_interval"]["method"] == "wilson"
    assert row["old_check_tolerance_used"] == 0.5


def test_non_applicable_checks_are_silently_skipped(temp_db):
    """Nothing to compare if either side didn't run -- must not log a
    meaningless row."""
    memory.record_shadow_mode_comparison("ionq", "forte-1", "OPENQASM 2.0;",
                                          {"applicable": False}, _new(True))
    result = memory.shadow_mode_disagreement_log()
    assert result["total_comparisons_logged"] == 0


def test_limit_caps_returned_disagreements(temp_db):
    for _ in range(5):
        memory.record_shadow_mode_comparison("ionq", "forte-1", "OPENQASM 2.0;",
                                              _old(True), _new(False))
    result = memory.shadow_mode_disagreement_log(limit=2)
    assert result["total_comparisons_logged"] == 5
    assert result["disagreement_count"] == 2


def test_source_defaults_to_verify_experiment_and_is_surfaced(temp_db):
    """The real call site (core/verifier.py's verify()) never passes
    source explicitly -- the default must be the accurate, real label."""
    memory.record_shadow_mode_comparison("ionq", "forte-1", "OPENQASM 2.0;",
                                          _old(True), _new(False))
    row = memory.shadow_mode_disagreement_log()["disagreements"][0]
    assert row["source"] == "verify_experiment"


def test_known_synthetic_source_is_excluded_from_the_log(temp_db):
    """Same provenance guarantee as memory_summary()/verdict_track_record()
    (see the 2026-08-27 predictions-table audit) -- a row tagged with a
    known-synthetic source must never count toward this log either."""
    memory.record_shadow_mode_comparison("ionq", "forte-1", "OPENQASM 2.0;",
                                          _old(True), _new(False), source="unit_test")
    result = memory.shadow_mode_disagreement_log()
    assert result["total_comparisons_logged"] == 0
    assert result["disagreement_count"] == 0
