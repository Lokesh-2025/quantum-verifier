"""
Loud startup schema-version check (Option B from the 2026-08-27 schema-drift
postmortem: docs/postmortem-draft-2026-08-27-schema-drift.md).

`CREATE TABLE IF NOT EXISTS` only ever checks whether a table exists -- never
whether an EXISTING table's columns match what the current code expects. This
project hit that exact gap twice in one night, on the same table
(shadow_mode_comparisons): a real, empty-but-stale table sat on disk after a
schema change, and the failure only surfaced as a confusing
sqlite3.OperationalError deep inside the next real write.

This closes the gap the cheap way: after every CREATE TABLE (IF NOT EXISTS),
compare the table's actual columns against what the code expects, and refuse
immediately and loudly on any mismatch -- rather than deferring the failure to
whatever real write happens to hit the missing/renamed column first. This does
NOT migrate anything; it only makes drift impossible to miss. A real migration
mechanism (SQLite's `user_version` pragma, or similar) is a separate, larger
follow-up (Option A in the same postmortem), not built here.
"""
import sqlite3


class SchemaDriftError(RuntimeError):
    """Raised when a table on disk doesn't match what the current code
    expects. Deliberately loud and immediate -- see module docstring."""


def assert_schema_matches(conn: sqlite3.Connection, table_name: str, expected_columns) -> None:
    actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}
    expected = set(expected_columns)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise SchemaDriftError(
            f"Schema drift detected in table '{table_name}': the table already "
            f"on disk does not match what this code currently expects.\n"
            f"  Missing columns:    {missing or 'none'}\n"
            f"  Unexpected columns: {unexpected or 'none'}\n"
            f"CREATE TABLE IF NOT EXISTS does not migrate an existing table -- "
            f"see docs/postmortem-draft-2026-08-27-schema-drift.md. If this "
            f"table is empty, it is likely safe to drop and let the code "
            f"recreate it correctly; if it holds real data, it needs a real "
            f"migration before this check will pass."
        )
