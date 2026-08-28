"""
Tests for _resolve_amplification_tolerance (core/verifier.py), added
2026-08-28 -- wiring recommend_tolerance's answer into the real decision
path instead of leaving it computed-but-unused. An explicit caller value
always wins outright; None resolves to a real, data-driven recommendation
from this project's own prediction-vs-reality history, falling back to the
same plain 0.5 default when there isn't yet enough real data.

Uses an isolated temp db (monkeypatched _DB_PATH) — never the real
experiment_memory.db. This is exactly the mistake the 2026-08-27
predictions-table postmortem was about; not repeating it here.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.memory as memory
import core.verifier as v
from core.verifier import _resolve_amplification_tolerance


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_experiment_memory.db")
    monkeypatch.setattr(memory, "_DB_PATH", db_path)
    return db_path


def _seed_real_predictions(provider, device, predicted, real, n):
    """Seed n real, complete prediction-vs-reality pairs -- source left at
    the real default ('verify_experiment'), never a synthetic marker."""
    for i in range(n):
        pred = memory.record_prediction(f"OPENQASM 2.0;\n// {i}", provider, device,
                                          predicted_amplification=predicted)
        memory.record_real_result(pred["prediction_id"], real_amplification=real)


# ---------------------------------------------------------------------------
# _resolve_amplification_tolerance, direct
# ---------------------------------------------------------------------------

def test_explicit_value_is_always_respected_exactly(temp_db):
    """An explicit value must win outright, regardless of what real data
    exists -- never silently overridden, even when 0.5 (the old default)
    is passed explicitly."""
    _seed_real_predictions("ionq", "forte-1", predicted=2.0, real=3.0, n=5)  # would recommend something else
    resolved, source = _resolve_amplification_tolerance("ionq", "forte-1", 0.5)
    assert resolved == 0.5
    assert source == "explicit"

    resolved2, source2 = _resolve_amplification_tolerance("ionq", "forte-1", 0.73)
    assert resolved2 == 0.73
    assert source2 == "explicit"


def test_none_with_no_real_data_falls_back_to_plain_default(temp_db):
    """No real history at all for this provider/device -- must fall back
    to the same plain 0.5 this project always used, not error or guess."""
    resolved, source = _resolve_amplification_tolerance("ionq", "a_device_with_no_history", None)
    assert resolved == 0.5
    assert source == "default (not enough data)"


def test_none_with_insufficient_real_data_falls_back_to_plain_default(temp_db):
    """Fewer real data points than MIN_DATA_POINTS_FOR_RECOMMENDATION (3)
    -- still an honest fallback, not a guess from too little data."""
    _seed_real_predictions("ionq", "forte-1", predicted=2.0, real=2.1, n=2)
    resolved, source = _resolve_amplification_tolerance("ionq", "forte-1", None)
    assert resolved == 0.5
    assert source == "default (not enough data)"


def test_none_with_enough_real_data_triggers_a_real_recommendation(temp_db):
    """Enough real data -- must actually use recommend_tolerance's real,
    data-driven number, not the hardcoded default, and say how many real
    points backed it."""
    # 5 points, each 25% off -> recommend_tolerance should recommend
    # something meaningfully above 0.25 (comfortably above observed error,
    # not equal to it -- see core/intelligence.py's own reasoning).
    _seed_real_predictions("ionq", "forte-1", predicted=10.0, real=8.0, n=5)
    resolved, source = _resolve_amplification_tolerance("ionq", "forte-1", None)
    assert resolved != 0.5
    assert resolved > 0.25  # comfortably above the observed 25% error, not equal to it
    assert source == "recommended (5 data points)"


def test_recommendation_is_scoped_to_the_exact_provider_and_device(temp_db):
    """Real data for a DIFFERENT device must not leak into this device's
    recommendation -- must fall back honestly instead."""
    _seed_real_predictions("ionq", "some-other-device", predicted=10.0, real=8.0, n=5)
    resolved, source = _resolve_amplification_tolerance("ionq", "forte-1", None)
    assert resolved == 0.5
    assert source == "default (not enough data)"


# ---------------------------------------------------------------------------
# End-to-end through verify() -- no real API needed, pure local simulation
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


def test_verify_surfaces_tolerance_used_and_source_explicit(temp_db):
    result = v.verify(BELL, provider="ionq", target_device="simulator", shots=2048,
                       expected_marked_bitstrings=["00", "11"], expected_amplification=2.0,
                       amplification_tolerance=0.5)
    assert result["tolerance_used"] == 0.5
    assert result["tolerance_source"] == "explicit"


def test_verify_surfaces_tolerance_used_and_source_default_when_none(temp_db):
    """No amplification_tolerance passed at all (the new None default) and
    no real history -- must resolve to the same plain 0.5 and say so."""
    result = v.verify(BELL, provider="ionq", target_device="simulator", shots=2048,
                       expected_marked_bitstrings=["00", "11"], expected_amplification=2.0)
    assert result["tolerance_used"] == 0.5
    assert result["tolerance_source"] == "default (not enough data)"


def test_verify_uses_a_real_recommendation_when_enough_history_exists(temp_db):
    _seed_real_predictions("ionq", "simulator", predicted=10.0, real=8.0, n=5)
    result = v.verify(BELL, provider="ionq", target_device="simulator", shots=2048,
                       expected_marked_bitstrings=["00", "11"], expected_amplification=2.0)
    assert result["tolerance_source"] == "recommended (5 data points)"
    assert result["tolerance_used"] != 0.5
