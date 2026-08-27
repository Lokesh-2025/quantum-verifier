"""
Regression tests for the two real schema migration gaps found tonight
(2026-08-27) on shadow_mode_comparisons, both from the same underlying
cause: `CREATE TABLE IF NOT EXISTS` never migrates an existing table's
schema, it only creates one if none exists yet. Twice tonight, a real
table on disk predated a code change to its CREATE TABLE statement, and
the next real write crashed with sqlite3.OperationalError.

These tests reconstruct each historical schema exactly (pulled directly
from git history, not reconstructed from memory) and confirm: the CURRENT
code's INSERT fails against each OLD schema (proving the failure mode is
real and reproducible), and succeeds against the CURRENT schema (proving
today's code is internally consistent on a fresh table). This does NOT
prove a migration mechanism exists -- none does; see the "systemic
finding" section of docs/overnight-report-2026-08-27.md for the (not yet
built) proposal. This only proves the failure mode is real, named, and
guarded against regressing further -- e.g. a future column addition that
doesn't account for existing real deployments.

Uses an isolated temp db (monkeypatched _DB_PATH) — never the real
experiment_memory.db.
"""
import os
import sqlite3
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


def _new(equivalent, p_value=0.01, tost_verdict=None):
    return {
        "applicable": True,
        "equivalent_at_alpha": equivalent,
        "p_value": p_value,
        "tost_verdict": tost_verdict or ("VERIFIED" if equivalent else "FAIL"),
        "total_shots": 1000,
        "marked_shots": 500,
        "claimed_probability": 0.5,
        "equivalence_margin": {"lower": 0.45, "upper": 0.55},
        "alpha": 0.05,
        "confidence_interval": {"lower": 0.47, "upper": 0.53, "method": "wilson"},
    }


# v0: the very first schema (commit fd76399) -- boolean agree/disagree only.
V0_SCHEMA_SQL = """
    CREATE TABLE shadow_mode_comparisons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        provider TEXT NOT NULL,
        target_device TEXT NOT NULL,
        circuit_hash TEXT NOT NULL,
        old_check_within_tolerance INTEGER,
        new_check_equivalent_at_alpha INTEGER,
        new_check_p_value REAL,
        agree INTEGER NOT NULL
    )
"""

# v1: the rich-stats rewrite (commit 375ee64) -- everything except `source`,
# which v2 (commit 36da78e, the current schema) added.
V1_SCHEMA_SQL = """
    CREATE TABLE shadow_mode_comparisons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        provider TEXT NOT NULL,
        target_device TEXT NOT NULL,
        circuit_hash TEXT NOT NULL,
        total_shots INTEGER NOT NULL,
        marked_shots INTEGER NOT NULL,
        claimed_probability REAL NOT NULL,
        equivalence_margin_lower REAL NOT NULL,
        equivalence_margin_upper REAL NOT NULL,
        alpha REAL NOT NULL,
        ci_lower REAL NOT NULL,
        ci_upper REAL NOT NULL,
        ci_method TEXT NOT NULL,
        p_value REAL NOT NULL,
        tost_verdict TEXT NOT NULL,
        old_check_within_tolerance INTEGER NOT NULL,
        old_check_tolerance_used REAL,
        agree INTEGER NOT NULL
    )
"""


def _create_with_raw_schema(db_path, schema_sql):
    conn = sqlite3.connect(db_path)
    conn.execute(schema_sql)
    conn.commit()
    conn.close()


def test_v0_boolean_only_schema_fails_against_current_code(temp_db):
    """Migration gap #1 (found during the second review pass): the very
    first schema (boolean agree/disagree only) cannot accept current
    code's INSERT, which expects 19 additional columns. This must FAIL,
    not silently succeed or silently drop data."""
    _create_with_raw_schema(temp_db, V0_SCHEMA_SQL)
    with pytest.raises(sqlite3.OperationalError):
        memory.record_shadow_mode_comparison("ionq", "forte-1", "OPENQASM 2.0;",
                                              _old(True), _new(True))


def test_v1_pre_source_schema_fails_against_current_code(temp_db):
    """Migration gap #2 (found overnight, 2026-08-27): the rich-stats
    schema before the `source` column was added. This is the exact
    real-world failure mode found and fixed tonight -- a real, empty
    table on disk had this schema, and the next real write would have
    crashed. Must FAIL here too, reproducibly."""
    _create_with_raw_schema(temp_db, V1_SCHEMA_SQL)
    with pytest.raises(sqlite3.OperationalError):
        memory.record_shadow_mode_comparison("ionq", "forte-1", "OPENQASM 2.0;",
                                              _old(True), _new(True))


def test_current_schema_succeeds(temp_db):
    """The current schema (created via the real _connect(), not raw SQL)
    must accept current code's INSERT without error -- proving today's
    code is internally consistent on a fresh table, the case that always
    worked and must keep working."""
    memory.record_shadow_mode_comparison("ionq", "forte-1", "OPENQASM 2.0;",
                                          _old(True), _new(True))  # must not raise
    result = memory.shadow_mode_disagreement_log()
    assert result["total_comparisons_logged"] == 1
