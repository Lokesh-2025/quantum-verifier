"""
Tests for the tool-invocation counter (mcp_server.py's _track_invocation
decorator + core/memory.py's record_tool_invocation), added 2026-08-28
implementing the design locked in the 2026-08-27 overnight report's Task 4
section. Real-usage tracking for the eventual 41->18 consolidation
decision -- deliberately minimal (tool name + when, nothing else).

Uses an isolated temp db (monkeypatched _DB_PATH) — never the real
experiment_memory.db. This is exactly the class of write path the
predictions-table postmortem was about; verified isolated here directly,
not assumed from conftest.py's blanket coverage.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.memory as memory
import mcp_server


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_experiment_memory.db")
    monkeypatch.setattr(memory, "_DB_PATH", db_path)
    return db_path


def test_a_decorated_tool_call_writes_exactly_one_row_with_the_correct_tool_name(temp_db):
    """check_taxonomy is a real, zero-argument, decorated tool -- cheap and
    deterministic to call directly."""
    mcp_server.check_taxonomy()
    conn = sqlite3.connect(temp_db)
    rows = conn.execute("SELECT tool_name, source FROM tool_invocations").fetchall()
    conn.close()
    assert rows == [("check_taxonomy", "real")]


def test_the_write_lands_in_the_isolated_temp_db_not_a_different_one(temp_db):
    """Verified directly, not assumed from conftest.py's blanket coverage
    -- confirm core.memory._DB_PATH is genuinely what got written to."""
    assert memory._DB_PATH == temp_db
    mcp_server.shadow_mode_disagreement_log()
    conn = sqlite3.connect(memory._DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM tool_invocations").fetchone()[0]
    conn.close()
    assert count == 1


def test_an_error_inside_the_wrapped_tool_does_not_skip_or_double_log(temp_db):
    """The tool itself must still raise/error normally -- tracking must not
    swallow the real failure -- and the invocation must be logged exactly
    once (not zero, not more than one) despite the error."""
    @mcp_server._track_invocation
    def _always_fails():
        raise ValueError("real failure inside the tool")

    with pytest.raises(ValueError, match="real failure inside the tool"):
        _always_fails()

    conn = sqlite3.connect(temp_db)
    rows = conn.execute("SELECT tool_name FROM tool_invocations").fetchall()
    conn.close()
    assert rows == [("_always_fails",)]


def test_multiple_calls_to_different_tools_each_log_their_own_real_name(temp_db):
    mcp_server.check_taxonomy()
    mcp_server.shadow_mode_disagreement_log()
    mcp_server.check_taxonomy()
    conn = sqlite3.connect(temp_db)
    rows = conn.execute("SELECT tool_name FROM tool_invocations ORDER BY id").fetchall()
    conn.close()
    assert rows == [("check_taxonomy",), ("shadow_mode_disagreement_log",), ("check_taxonomy",)]


def test_a_tracking_failure_never_breaks_the_real_tool_call(temp_db, monkeypatch):
    """Tracking is a secondary, best-effort signal -- if recording the
    invocation itself throws, the real tool's result must still come back
    normally, not be swallowed or replaced with an error."""
    def _broken_record(*args, **kwargs):
        raise RuntimeError("simulated tracking failure")

    monkeypatch.setattr("core.memory.record_tool_invocation", _broken_record)
    result = mcp_server.check_taxonomy()  # must not raise despite tracking being broken
    assert result  # the real tool result still comes back
