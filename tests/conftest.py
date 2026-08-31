"""
Session-wide test isolation for core/memory.py's sqlite database.

Added 2026-08-27 after a real bug: verify() now calls
record_shadow_mode_comparison() as a side effect (core/verifier.py), and
nothing was stopping ordinary tests that call v.verify() directly (most of
tests/test_verifier.py, tests/test_ground_truth_significance.py, etc.) from
silently writing test-generated rows into the REAL experiment_memory.db —
confirmed directly: 12 rows of pytest-fixture circuits (device names like
"simulator", "forte-1") had already landed in the real db from earlier
test runs this session, polluting the exact log that's supposed to be a
clean record of real experiments for later review.

This autouse, session-scoped fixture monkeypatches core.memory._DB_PATH to
a fresh temp file for the entire test session, so no test run — now or in
the future — can touch the real db just by calling a function that happens
to log to it. Individual tests that need their OWN isolated db (e.g.
tests/test_verdict_track_record.py, which asserts on exact row counts) can
still layer their own temp_db fixture on top; this just guarantees the
floor is always an isolated file, never the real one, by default.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True, scope="session")
def _isolate_experiment_memory_db(tmp_path_factory):
    import core.memory as memory
    db_path = str(tmp_path_factory.mktemp("memory") / "test_experiment_memory.db")
    original = memory._DB_PATH
    memory._DB_PATH = db_path
    yield
    memory._DB_PATH = original


@pytest.fixture(autouse=True, scope="session")
def _block_real_turso_during_tests():
    """
    Added 2026-08-30 alongside providers/ibm.py's _run_query() Turso
    integration. mcp_server.py calls load_dotenv() at import time, and at
    least one test (test_tool_invocation_tracking.py) imports mcp_server —
    that would load the REAL TURSO_DATABASE_URL/TURSO_AUTH_TOKEN from .env
    into this process's environment, silently letting test_drift_gate.py
    and anything else that monkeypatches DB_PATH expecting an isolated
    local db instead hit the real, live, shared Turso database.

    Patches _get_turso_client itself (not just the env vars) — robust
    regardless of WHEN load_dotenv() fires relative to this fixture, since
    popping env vars once at session start wouldn't survive a later
    load_dotenv() call re-populating them.
    """
    import providers.ibm as ibm
    original = ibm._get_turso_client
    ibm._get_turso_client = lambda: None
    yield
    ibm._get_turso_client = original
