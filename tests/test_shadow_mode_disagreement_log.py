"""
Tests for record_shadow_mode_comparison / shadow_mode_disagreement_log
(core/memory.py), added 2026-08-27 per external review's item 4 — logging,
for free, every time the old tolerance-band check (ground_truth_check) and
the new statistical equivalence test (ground_truth_significance_test)
agree or disagree. Doesn't need a real hardware result to be useful.

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


def _new(equivalent, p_value=0.01):
    return {"applicable": True, "equivalent_at_alpha": equivalent, "p_value": p_value}


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


def test_disagreement_is_logged_and_surfaced(temp_db):
    memory.record_shadow_mode_comparison("ionq", "forte-1", "OPENQASM 2.0;",
                                          _old(True), _new(False, p_value=0.4))
    result = memory.shadow_mode_disagreement_log()
    assert result["total_comparisons_logged"] == 1
    assert result["disagreement_count"] == 1
    row = result["disagreements"][0]
    assert row["old_check_said_within_tolerance"] is True
    assert row["new_check_said_equivalent"] is False
    assert row["new_check_p_value"] == 0.4


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
