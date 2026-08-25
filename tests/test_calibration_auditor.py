"""
Tests for audit_calibration_telemetry (providers/ibm.py), added 2026-08-24.
Generalizes the exact bug this project just fixed (calibration fields
silently null/broken with nothing checking the feed itself) into a
reusable, ongoing check.

All tests use an isolated temp db — no real IBM API calls.
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


def _insert_device_snapshot(db_path, name, ts, **fields):
    cols = ["ts", "name"] + list(fields.keys())
    placeholders = ",".join("?" * len(cols))
    con = sqlite3.connect(db_path)
    con.execute(
        f"INSERT INTO device_snapshots ({','.join(cols)}) VALUES ({placeholders})",
        [ts, name] + list(fields.values()),
    )
    con.commit()
    con.close()


def _insert_qubit_snapshot(db_path, device, qubit, prop, value, measured_at):
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT OR IGNORE INTO qubit_snapshots "
        "(device_name, qubit_index, property_name, value, unit, vendor_measured_at, polled_at) "
        "VALUES (?, ?, ?, ?, 'us', ?, ?)",
        (device, qubit, prop, value, measured_at, measured_at),
    )
    con.commit()
    con.close()


def _iso(dt):
    return dt.isoformat()


def test_clean_history_finds_nothing(temp_db):
    now = datetime.now(timezone.utc)
    values = [0.011, 0.0112, 0.0109, 0.0115, 0.0108, 0.0113, 0.0107]
    for i, v in enumerate(values):
        _insert_device_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=i)), avg_cx_error=v)
    result = ibm.audit_calibration_telemetry("ibm_fez")
    assert result["n_findings"] == 0
    assert result["verdict"] == "trust"


def test_frozen_value_detected(temp_db):
    now = datetime.now(timezone.utc)
    # 5 identical recent readings, but real variance further back
    for i in range(5):
        _insert_device_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=i)), avg_cx_error=0.0123)
    for i in range(5, 10):
        _insert_device_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=i)), avg_cx_error=0.011 + i * 0.0005)
    result = ibm.audit_calibration_telemetry("ibm_fez")
    checks = [f["check"] for f in result["findings"]]
    assert "frozen_value" in checks
    assert result["verdict"] == "review_before_trusting"


def test_suspicious_placeholder_detected(temp_db):
    now = datetime.now(timezone.utc)
    _insert_device_snapshot(temp_db, "ibm_fez", _iso(now), avg_readout_error=0.5)
    result = ibm.audit_calibration_telemetry("ibm_fez")
    checks = [f["check"] for f in result["findings"]]
    assert "suspicious_placeholder" in checks


def test_physics_invariant_violation_detected(temp_db):
    now = datetime.now(timezone.utc)
    # T2 > 2*T1 is not physically possible
    _insert_device_snapshot(temp_db, "ibm_fez", _iso(now), median_t1_us=50.0, median_t2_us=200.0)
    result = ibm.audit_calibration_telemetry("ibm_fez")
    checks = [f["check"] for f in result["findings"]]
    assert "physics_invariant_violation" in checks


def test_physically_valid_t1_t2_passes(temp_db):
    now = datetime.now(timezone.utc)
    _insert_device_snapshot(temp_db, "ibm_fez", _iso(now), median_t1_us=150.0, median_t2_us=120.0)
    result = ibm.audit_calibration_telemetry("ibm_fez")
    checks = [f["check"] for f in result["findings"]]
    assert "physics_invariant_violation" not in checks


def test_per_qubit_copy_paste_detected(temp_db):
    now = datetime.now(timezone.utc)
    measured_at = _iso(now)
    for qi in range(20):
        _insert_qubit_snapshot(temp_db, "ibm_fez", qi, "T1", 145.0, measured_at)  # all identical
    result = ibm.audit_calibration_telemetry("ibm_fez")
    checks = [f["check"] for f in result["findings"]]
    assert "per_qubit_copy_paste" in checks


def test_real_per_qubit_spread_passes(temp_db):
    now = datetime.now(timezone.utc)
    measured_at = _iso(now)
    for qi in range(20):
        _insert_qubit_snapshot(temp_db, "ibm_fez", qi, "T1", 100.0 + qi * 3.7, measured_at)  # real spread
    result = ibm.audit_calibration_telemetry("ibm_fez")
    checks = [f["check"] for f in result["findings"]]
    assert "per_qubit_copy_paste" not in checks


def test_trust_score_decreases_with_more_findings(temp_db):
    now = datetime.now(timezone.utc)
    _insert_device_snapshot(temp_db, "ibm_fez", _iso(now), median_t1_us=50.0, median_t2_us=200.0,
                             avg_readout_error=0.5)
    result = ibm.audit_calibration_telemetry("ibm_fez")
    assert result["n_findings"] >= 2
    assert result["telemetry_trust_score"] < 1.0
