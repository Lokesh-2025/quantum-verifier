"""
Tests for verdict_track_record (core/memory.py), added 2026-08-24 — a real
hit-rate computed from actual recorded prediction-vs-reality pairs, not a
claimed one: "when this tool's verdict would have said GO, how often was
that actually true?"

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


def _record_and_complete(provider, device, predicted, real, marked=None):
    pred = memory.record_prediction("OPENQASM 2.0;", provider, device,
                                      predicted_amplification=predicted, marked_bitstrings=marked)
    memory.record_real_result(pred["prediction_id"], real)


def test_no_data_reports_zero_not_a_crash(temp_db):
    result = memory.verdict_track_record()
    assert result["n"] == 0


def test_hit_when_real_within_tolerance(temp_db):
    _record_and_complete("ionq", "forte-1", predicted=2.0, real=2.1)
    result = memory.verdict_track_record(tolerance=0.5)
    assert result["overall_n"] == 1
    assert result["overall_hit_rate"] == 1.0


def test_miss_when_real_outside_tolerance(temp_db):
    _record_and_complete("ionq", "forte-1", predicted=2.0, real=10.0)
    result = memory.verdict_track_record(tolerance=0.5)
    assert result["overall_n"] == 1
    assert result["overall_hit_rate"] == 0.0


def test_mixed_hits_and_misses_computes_real_rate(temp_db):
    _record_and_complete("ionq", "forte-1", predicted=2.0, real=2.1)   # hit
    _record_and_complete("ionq", "forte-1", predicted=2.0, real=2.05)  # hit
    _record_and_complete("ionq", "forte-1", predicted=2.0, real=8.0)   # miss
    result = memory.verdict_track_record(tolerance=0.5)
    assert result["overall_n"] == 3
    assert result["overall_hit_rate"] == pytest.approx(2 / 3, abs=0.001)


def test_breakdown_by_provider_device(temp_db):
    _record_and_complete("ionq", "forte-1", predicted=2.0, real=2.1)
    _record_and_complete("ionq", "forte-enterprise-1", predicted=3.0, real=20.0)
    result = memory.verdict_track_record(tolerance=0.5)
    assert result["by_provider_device"]["ionq/forte-1"]["hit_rate"] == 1.0
    assert result["by_provider_device"]["ionq/forte-enterprise-1"]["hit_rate"] == 0.0


def test_provider_filter_isolates_results(temp_db):
    _record_and_complete("ionq", "forte-1", predicted=2.0, real=2.1)
    result = memory.verdict_track_record(provider="ibm")
    assert result["n"] == 0


def test_predictions_without_real_result_are_excluded(temp_db):
    memory.record_prediction("OPENQASM 2.0;", "ionq", "forte-1", predicted_amplification=2.0)
    result = memory.verdict_track_record()
    assert result["n"] == 0


def test_response_includes_ionq_only_scope_caveat(temp_db):
    _record_and_complete("ionq", "forte-1", predicted=2.0, real=2.1)
    result = memory.verdict_track_record()
    assert "IonQ" in result["scope_caveat"]


def test_known_synthetic_sources_are_excluded_from_the_real_track_record(temp_db):
    """Added 2026-08-27, after a real audit found 42 of 45 rows in the
    REAL predictions table were an identical repeated test fixture
    (source='unit_test'), silently making a live '1.0 hit rate' 93%
    synthetic. _NON_REAL_SOURCES must keep that from ever counting again,
    no matter how a caller's test data ends up tagged."""
    pred = memory.record_prediction("OPENQASM 2.0;", "ionq", "forte-1",
                                     predicted_amplification=10.0, source="unit_test")
    memory.record_real_result(pred["prediction_id"], real_amplification=8.0)  # would be a "hit"
    result = memory.verdict_track_record()
    assert result["n"] == 0, "a known-synthetic source must never count toward the real track record"


def test_a_mix_of_real_and_synthetic_only_counts_the_real_ones(temp_db):
    _record_and_complete("ionq", "forte-1", predicted=2.0, real=2.1)  # real, default source
    pred = memory.record_prediction("OPENQASM 2.0;", "ionq", "forte-1",
                                     predicted_amplification=10.0, source="unit_test")
    memory.record_real_result(pred["prediction_id"], real_amplification=8.0)
    result = memory.verdict_track_record()
    assert result["overall_n"] == 1


def test_memory_summary_also_excludes_known_synthetic_sources(temp_db):
    """Same filter, same reason, the other function that answers 'how
    trustworthy is this tool' — must not silently diverge from
    verdict_track_record's behavior."""
    pred = memory.record_prediction("OPENQASM 2.0;", "ionq", "forte-1",
                                     predicted_amplification=10.0, source="unit_test")
    memory.record_real_result(pred["prediction_id"], real_amplification=8.0)
    result = memory.memory_summary()
    assert result["predictions_with_real_results"] == 0
    assert result["total_predictions_ever_recorded"] == 0
