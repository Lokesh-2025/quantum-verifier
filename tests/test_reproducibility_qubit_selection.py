"""
Tests for best_qubits_for_reproducibility (providers/ibm.py), added
2026-08-24 — favors stable qubits over momentarily-good ones for
start_repro_experiment/repro_score, using real per-qubit T1 history.

best_qubits() itself makes a real live IBM API call, so these tests
monkeypatch it directly rather than mocking the whole qiskit stack —
the thing actually being tested here is the volatility-scoring and
re-ranking logic on top, not best_qubits() itself (which has its own
existing tests elsewhere).
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import providers.ibm as ibm


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_ibm_history.db")
    monkeypatch.setattr(ibm, "DB_PATH", db_path)
    ibm._init_db()
    return db_path


def _seed_t1_history(db_path, device, qubit, values):
    now = datetime.now(timezone.utc)
    con = sqlite3.connect(db_path)
    for i, v in enumerate(values):
        measured_at = (now - timedelta(days=len(values) - i)).isoformat()
        con.execute(
            "INSERT OR IGNORE INTO qubit_snapshots "
            "(device_name, qubit_index, property_name, value, unit, vendor_measured_at, polled_at) "
            "VALUES (?, ?, 'T1', ?, 'us', ?, ?)",
            (device, qubit, v, measured_at, measured_at),
        )
    con.commit()
    con.close()


def _fake_best_qubits(qubit_scores):
    """qubit_scores: dict of {qubit_index: score} -- returns a best_qubits()-shaped response."""
    entries = [
        {"qubit": q, "score": s, "readout_error": 0.005, "best_cx_error": 0.002,
         "t1_us": 100.0, "t2_us": 90.0}
        for q, s in qubit_scores.items()
    ]
    entries.sort(key=lambda e: e["score"])
    return {"device": "ibm_fez", "n": len(entries), "scoring": "fake", "best_qubits": entries,
            "connectivity": {}}


def test_low_confidence_when_too_little_history(temp_db, monkeypatch):
    monkeypatch.setattr(ibm, "best_qubits", lambda name, n: _fake_best_qubits({0: 0.01, 1: 0.02}))
    _seed_t1_history(temp_db, "ibm_fez", 0, [145.0])  # only 1 reading
    result = ibm.best_qubits_for_reproducibility("ibm_fez", n=2)
    entries = {e["qubit"]: e for e in result["best_qubits_for_reproducibility"]}
    assert entries[0]["low_confidence"] is True
    assert entries[1]["low_confidence"] is True  # no history at all


def test_volatile_qubit_ranks_below_stable_one_despite_better_current_score(temp_db, monkeypatch):
    """Qubit 0 has the better CURRENT score but is wildly volatile.
    Qubit 1 has a slightly worse current score but is rock-stable.
    The stability-adjusted ranking should prefer qubit 1."""
    monkeypatch.setattr(ibm, "best_qubits", lambda name, n: _fake_best_qubits({0: 0.005, 1: 0.006}))
    _seed_t1_history(temp_db, "ibm_fez", 0, [50.0, 200.0, 30.0, 180.0, 40.0])  # wild swings
    _seed_t1_history(temp_db, "ibm_fez", 1, [100.0, 101.0, 99.0, 100.5, 99.5])  # rock stable
    result = ibm.best_qubits_for_reproducibility("ibm_fez", n=2)
    ranked = result["best_qubits_for_reproducibility"]
    assert ranked[0]["qubit"] == 1, "the stable qubit should rank first despite the worse raw score"
    assert ranked[0]["low_confidence"] is False
    assert ranked[1]["low_confidence"] is False


def test_coefficient_of_variation_computed_correctly(temp_db, monkeypatch):
    monkeypatch.setattr(ibm, "best_qubits", lambda name, n: _fake_best_qubits({0: 0.01}))
    _seed_t1_history(temp_db, "ibm_fez", 0, [100.0, 100.0, 100.0, 100.0])  # zero variance
    result = ibm.best_qubits_for_reproducibility("ibm_fez", n=1)
    entry = result["best_qubits_for_reproducibility"][0]
    assert entry["t1_coefficient_of_variation"] == 0.0
    assert entry["stability_adjusted_score"] == entry["score"]  # no penalty when perfectly stable


def test_propagates_error_from_best_qubits(temp_db, monkeypatch):
    monkeypatch.setattr(ibm, "best_qubits", lambda name, n: {"error": "no calibration data"})
    result = ibm.best_qubits_for_reproducibility("ibm_fez")
    assert "error" in result
