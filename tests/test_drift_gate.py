"""
Tests for the automatic pre-submission drift gate added to providers/ibm.py
2026-08-24 — ported from quantum-hardware-mcp's equivalent fix, adapted to
this project's own smaller local db (ibm_history.db) rather than sharing
files across the two repos.

Also covers the real bug found while porting: _save_snapshots() previously
only wrote 2 of ~9 relevant columns, and get_device_details() computed T1/T2
under different key names (avg_t1_us/avg_t2_us) than what _save_snapshots
and get_alerts actually read (median_t1_us/median_t2_us) — meaning every
row ever saved before this fix had every calibration field null, so
get_alerts() could never have fired on real data. Confirmed directly
against the real local db: 72/72 existing rows had avg_cx_error IS NULL.

All tests here use an isolated temp db (monkeypatched DB_PATH) — no real
IBM API calls, no real hardware, no touching the real ibm_history.db.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import providers.ibm as ibm


BELL_QASM2 = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q[0] -> c[0];
measure q[1] -> c[1];
""".strip()


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_ibm_history.db")
    monkeypatch.setattr(ibm, "DB_PATH", db_path)
    ibm._init_db()
    return db_path


def _insert_snapshot(db_path, name, ts, **fields):
    cols = ["ts", "name"] + list(fields.keys())
    placeholders = ",".join("?" * len(cols))
    con = sqlite3.connect(db_path)
    con.execute(
        f"INSERT INTO device_snapshots ({','.join(cols)}) VALUES ({placeholders})",
        [ts, name] + list(fields.values()),
    )
    con.commit()
    con.close()


def _iso(dt):
    return dt.isoformat()


# ---------------------------------------------------------------------------
# _save_snapshots — the real bug: key-name mismatch + dropped columns
# ---------------------------------------------------------------------------

def test_save_snapshots_persists_cx_and_readout_error(temp_db):
    ibm._save_snapshots([{"name": "ibm_fez", "num_qubits": 156, "operational": True,
                           "avg_cx_error": 0.011, "avg_readout_error": 0.008}])
    row = sqlite3.connect(temp_db).execute(
        "SELECT avg_cx_error, avg_readout_error FROM device_snapshots WHERE name='ibm_fez'"
    ).fetchone()
    assert row == (0.011, 0.008)


def test_save_snapshots_accepts_avg_t1_us_key_from_get_device_details(temp_db):
    """The real bug: get_device_details() produces avg_t1_us/avg_t2_us, but
    the table (and get_alerts) use median_t1_us/median_t2_us. Before the
    fix, this data was silently dropped on the floor."""
    ibm._save_snapshots([{"name": "ibm_fez", "avg_t1_us": 145.2, "avg_t2_us": 98.7}])
    row = sqlite3.connect(temp_db).execute(
        "SELECT median_t1_us, median_t2_us FROM device_snapshots WHERE name='ibm_fez'"
    ).fetchone()
    assert row == (145.2, 98.7)


def test_save_snapshots_still_accepts_median_key_directly(temp_db):
    ibm._save_snapshots([{"name": "ibm_fez", "median_t1_us": 200.0, "median_t2_us": 150.0}])
    row = sqlite3.connect(temp_db).execute(
        "SELECT median_t1_us, median_t2_us FROM device_snapshots WHERE name='ibm_fez'"
    ).fetchone()
    assert row == (200.0, 150.0)


# ---------------------------------------------------------------------------
# _recent_drift_alert
# ---------------------------------------------------------------------------

def test_no_alert_when_no_history(temp_db):
    assert ibm._recent_drift_alert("ibm_fez") is None


def test_no_alert_when_stable(temp_db):
    now = datetime.now(timezone.utc)
    _insert_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=10)), avg_cx_error=0.01)
    _insert_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=2)), avg_cx_error=0.0102)
    assert ibm._recent_drift_alert("ibm_fez") is None


def test_detects_fresh_cx_error_spike(temp_db):
    now = datetime.now(timezone.utc)
    _insert_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=10)), avg_cx_error=0.01)
    _insert_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=2)), avg_cx_error=0.05)
    result = ibm._recent_drift_alert("ibm_fez")
    assert result is not None
    assert result["type"] == "cx_error_spike"


def test_detects_fresh_readout_error_spike(temp_db):
    now = datetime.now(timezone.utc)
    _insert_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=10)), avg_readout_error=0.01)
    _insert_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=2)), avg_readout_error=0.04)
    result = ibm._recent_drift_alert("ibm_fez")
    assert result is not None
    assert result["type"] == "readout_error_spike"


def test_detects_fresh_t1_drop(temp_db):
    now = datetime.now(timezone.utc)
    _insert_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=10)), median_t1_us=150.0)
    _insert_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=2)), median_t1_us=100.0)
    result = ibm._recent_drift_alert("ibm_fez")
    assert result is not None
    assert result["type"] == "t1_drop"


def test_stale_alert_outside_window_ignored(temp_db):
    now = datetime.now(timezone.utc)
    _insert_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=48)), avg_cx_error=0.01)
    _insert_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=30)), avg_cx_error=0.05)
    assert ibm._recent_drift_alert("ibm_fez", hours=24) is None


def test_alert_on_different_device_ignored(temp_db):
    now = datetime.now(timezone.utc)
    _insert_snapshot(temp_db, "qpu.forte-1", _iso(now - timedelta(hours=10)), avg_cx_error=0.01)
    _insert_snapshot(temp_db, "qpu.forte-1", _iso(now - timedelta(hours=2)), avg_cx_error=0.05)
    assert ibm._recent_drift_alert("ibm_fez") is None


# ---------------------------------------------------------------------------
# submit_job gate — monkeypatch _recent_drift_alert so no live IBM call
# ---------------------------------------------------------------------------

def test_submit_job_blocked_by_fresh_drift_alert(temp_db, monkeypatch):
    fake_alert = {"ts": "2026-08-24T00:00:00+00:00", "type": "cx_error_spike",
                  "prev_value": 0.01, "curr_value": 0.05}
    monkeypatch.setattr(ibm, "_recent_drift_alert", lambda name, hours=24: fake_alert)

    def _boom(*a, **k):
        raise AssertionError("submit_job must not reach _get_service() when blocked")
    monkeypatch.setattr(ibm, "_get_service", _boom)

    result = ibm.submit_job("ibm_fez", BELL_QASM2, shots=128)
    assert "error" in result
    assert result["drift_alert"]["type"] == "cx_error_spike"


def test_submit_job_override_bypasses_gate(temp_db, monkeypatch):
    fake_alert = {"ts": "2026-08-24T00:00:00+00:00", "type": "cx_error_spike",
                  "prev_value": 0.01, "curr_value": 0.05}
    monkeypatch.setattr(ibm, "_recent_drift_alert", lambda name, hours=24: fake_alert)

    def _sentinel(*a, **k):
        raise RuntimeError("reached real submission path")
    monkeypatch.setattr(ibm, "_get_service", _sentinel)

    with pytest.raises(RuntimeError, match="reached real submission path"):
        ibm.submit_job("ibm_fez", BELL_QASM2, shots=128, confirm_despite_drift_alert=True)


def test_submit_job_no_alert_does_not_block(temp_db, monkeypatch):
    monkeypatch.setattr(ibm, "_recent_drift_alert", lambda name, hours=24: None)

    def _sentinel(*a, **k):
        raise RuntimeError("reached real submission path")
    monkeypatch.setattr(ibm, "_get_service", _sentinel)

    with pytest.raises(RuntimeError, match="reached real submission path"):
        ibm.submit_job("ibm_fez", BELL_QASM2, shots=128)


# ---------------------------------------------------------------------------
# get_alerts now covers cx/readout spikes too, not just T1/T2
# ---------------------------------------------------------------------------

def test_get_alerts_reports_cx_error_spike(temp_db):
    now = datetime.now(timezone.utc)
    _insert_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=10)), avg_cx_error=0.01)
    _insert_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=2)), avg_cx_error=0.05)
    result = ibm.get_alerts(device_name="ibm_fez", days=1)
    types = [a["type"] for a in result["alerts"]]
    assert "cx_error_spike" in types


def test_get_alerts_reports_readout_error_spike(temp_db):
    now = datetime.now(timezone.utc)
    _insert_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=10)), avg_readout_error=0.01)
    _insert_snapshot(temp_db, "ibm_fez", _iso(now - timedelta(hours=2)), avg_readout_error=0.04)
    result = ibm.get_alerts(device_name="ibm_fez", days=1)
    types = [a["type"] for a in result["alerts"]]
    assert "readout_error_spike" in types
