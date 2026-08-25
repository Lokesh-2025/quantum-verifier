"""
Tests for check_chip_identity / _qubit_fingerprint_vector (providers/ibm.py),
ported from quantum-hardware-mcp 2026-08-24. Uses a PROVISIONAL fixed
threshold (0.5) rather than a gap-calibrated baseline, since this
project's per-qubit archive is too new for the same empirical study the
main tool did against 831 real days — confirmed live: at compare_days_back=2
this project's real archive already gives real, working correlations
(0.90-1.0 on an unchanged chip), but compare_days_back=14 currently has
too few overlapping qubits (real, honest data sparsity, not a bug).

All tests here use an isolated temp db — no real IBM API calls.
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


def _seed_qubit(db_path, device, qubit, prop, value, measured_at):
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT OR IGNORE INTO qubit_snapshots "
        "(device_name, qubit_index, property_name, value, unit, vendor_measured_at, polled_at) "
        "VALUES (?, ?, ?, ?, 'us', ?, ?)",
        (device, qubit, prop, value, measured_at, measured_at),
    )
    con.commit()
    con.close()


def _seed_pair(db_path, device, q1, q2, value, measured_at):
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT OR IGNORE INTO pair_snapshots "
        "(device_name, qubit1, qubit2, gate_name, property_name, value, unit, "
        " vendor_measured_at, polled_at) VALUES (?, ?, ?, 'cz', 'gate_error', ?, '', ?, ?)",
        (device, q1, q2, value, measured_at, measured_at),
    )
    con.commit()
    con.close()


def _iso(dt):
    return dt.isoformat()


def _seed_unchanged_chip(db_path, device, n_qubits, now, days_ago_list):
    for days_ago in days_ago_list:
        measured_at = _iso(now - timedelta(days=days_ago))
        for qi in range(n_qubits):
            _seed_qubit(db_path, device, qi, "T1", 30.0 + qi * 0.5, measured_at)
            _seed_qubit(db_path, device, qi, "T2", 25.0 + qi * 0.4, measured_at)
            _seed_qubit(db_path, device, qi, "readout_error", 0.01 + qi * 0.0001, measured_at)
        for qi in range(n_qubits - 1):
            _seed_pair(db_path, device, qi, qi + 1, 0.005 + qi * 0.00005, measured_at)


def test_no_history_returns_clean_error(temp_db):
    result = ibm.check_chip_identity("nonexistent_device")
    assert "error" in result
    assert "No per-qubit history" in result["error"]


def test_unchanged_chip_reads_as_consistent(temp_db):
    now = datetime.now(timezone.utc)
    _seed_unchanged_chip(temp_db, "ibm_fez", n_qubits=40, now=now, days_ago_list=[0, 2, 5, 7])
    result = ibm.check_chip_identity("ibm_fez", compare_days_back=5)
    assert result["verdict"] == "consistent"
    assert result["avg_raw_correlation"] > 0.9


def test_scrambled_qubit_order_is_not_consistent(temp_db):
    """Same value pool, reassigned to different qubit indices at the more
    recent date — simulates a relabeling / identity change."""
    now = datetime.now(timezone.utc)
    n = 40
    old_date = _iso(now - timedelta(days=5))
    new_date = _iso(now)
    for qi in range(n):
        _seed_qubit(temp_db, "ibm_fez", qi, "T1", 30.0 + qi * 0.5, old_date)
        _seed_qubit(temp_db, "ibm_fez", qi, "T2", 25.0 + qi * 0.4, old_date)
    for qi in range(n):
        source = n - 1 - qi
        _seed_qubit(temp_db, "ibm_fez", qi, "T1", 30.0 + source * 0.5, new_date)
        _seed_qubit(temp_db, "ibm_fez", qi, "T2", 25.0 + source * 0.4, new_date)
    result = ibm.check_chip_identity("ibm_fez", compare_days_back=5)
    assert result["verdict"] != "consistent"
    assert result["avg_raw_correlation"] < 0.5


def test_not_enough_qubits_returns_error(temp_db):
    now = datetime.now(timezone.utc)
    _seed_unchanged_chip(temp_db, "ibm_fez", n_qubits=3, now=now, days_ago_list=[0, 5])
    result = ibm.check_chip_identity("ibm_fez", compare_days_back=5)
    assert "error" in result


def test_response_includes_provisional_threshold_caveat(temp_db):
    now = datetime.now(timezone.utc)
    _seed_unchanged_chip(temp_db, "ibm_fez", n_qubits=40, now=now, days_ago_list=[0, 5])
    result = ibm.check_chip_identity("ibm_fez", compare_days_back=5)
    assert "PROVISIONAL" in result["caveat"]
    assert result["threshold_used"] == 0.5


# ---------------------------------------------------------------------------
# _save_qubit_and_pair_snapshot -- the collector-side write path
# ---------------------------------------------------------------------------

class _FakeNduv:
    def __init__(self, name, value, unit, date):
        self.name, self.value, self.unit, self.date = name, value, unit, date


class _FakeGate:
    def __init__(self, gate, qubits, parameters):
        self.gate, self.qubits, self.parameters = gate, qubits, parameters


class _FakeProps:
    def __init__(self, qubits, gates, last_update_date):
        self.qubits, self.gates, self.last_update_date = qubits, gates, last_update_date


def test_save_qubit_and_pair_snapshot_writes_real_rows(temp_db):
    now = datetime.now(timezone.utc)
    props = _FakeProps(
        qubits=[[_FakeNduv("T1", 145.0, "us", now)], [_FakeNduv("T1", 150.0, "us", now)]],
        gates=[_FakeGate("cz", [0, 1], [_FakeNduv("gate_error", 0.01, "", now)])],
        last_update_date=now,
    )
    ibm._save_qubit_and_pair_snapshot("ibm_fez", props)
    con = sqlite3.connect(temp_db)
    n_qubit_rows = con.execute("SELECT COUNT(*) FROM qubit_snapshots WHERE device_name='ibm_fez'").fetchone()[0]
    n_pair_rows = con.execute("SELECT COUNT(*) FROM pair_snapshots WHERE device_name='ibm_fez'").fetchone()[0]
    n_raw_rows = con.execute("SELECT COUNT(*) FROM raw_properties_archive WHERE device_name='ibm_fez'").fetchone()[0]
    assert n_qubit_rows == 2
    assert n_pair_rows == 1
    assert n_raw_rows == 1


def test_save_qubit_and_pair_snapshot_dedupes_on_rerun(temp_db):
    now = datetime.now(timezone.utc)
    props = _FakeProps(
        qubits=[[_FakeNduv("T1", 145.0, "us", now)]],
        gates=[],
        last_update_date=now,
    )
    ibm._save_qubit_and_pair_snapshot("ibm_fez", props)
    ibm._save_qubit_and_pair_snapshot("ibm_fez", props)  # identical -- should no-op
    con = sqlite3.connect(temp_db)
    n = con.execute("SELECT COUNT(*) FROM qubit_snapshots WHERE device_name='ibm_fez'").fetchone()[0]
    assert n == 1
