<!--
DRAFT ONLY. Not merged into POSTMORTEMS.md. Written autonomously overnight
(2026-08-27) per an explicit prompt instruction to draft, not publish. A
human should read, edit, and decide whether/how to fold this into
POSTMORTEMS.md before it's public.
-->

## 2026-08-27 (overnight) — Two schema migration gaps on the same table, and a second, never-audited data store

**Severity:** One live crash bug (caught before it fired on a real call). One data-quality gap (real data, uncontrolled collection cadence — not fabrication). Neither reached a real experiment or a real verdict.

**Status:** The crash bug is fixed. The data-quality gap is reported, not yet fixed — a deliberate choice, explained below.

### Summary

While auditing the project's own data stores overnight (prompted by the predictions-table corruption found earlier the same night — see the entry above), two more issues turned up, in a part of the codebase that incident hadn't touched:

1. The real `shadow_mode_comparisons` table (the equivalence-test agreement log, added earlier the same night) was sitting on disk with a schema from an *earlier* point in the evening — missing the `source` column added in a later fix. It had zero rows, so nothing was lost, but the very next real call to `verify()` would have thrown `sqlite3.OperationalError: table shadow_mode_comparisons has no column named source`. This is the second time in one night the same underlying mechanism caused this: `CREATE TABLE IF NOT EXISTS` never migrates an existing table's schema, it only creates one if none exists.

2. A second, previously-unaudited persistent data store, `ibm_history.db` (3.8MB, ~14,500 rows across 8 tables — device snapshots, per-qubit and per-pair calibration history, job submissions), turned out to have the same *class* of unisolated-test-write exposure as the predictions table did, though with real, not fabricated, data: 2 of 6 relevant test files call live IBM API functions without isolating the database, so every test run injects extra real snapshots. 78% of `device_snapshots`' distinct collection timestamps sit within 10 minutes of the previous one — inconsistent with the intended 6-hourly collection cadence, consistent with repeated test-triggered writes.

### How it was found

Not by design, again. The predictions-table postmortem's own lesson — "a provenance field that nothing reads is decoration" — prompted an explicit instruction to audit *every* persistent data store the next time, not just the one already known about. Enumerating stores directly (rather than assuming the known one was the only one) surfaced `ibm_history.db`. Inspecting the newer `shadow_mode_comparisons` table for the same audit turned up the stale-schema crash risk.

### Root cause

**Issue 1 (crash bug):** `core/memory.py`'s `_connect()` always runs `CREATE TABLE IF NOT EXISTS`, which is a no-op if the table already exists — including if it exists with an *older* schema. Across one evening, `shadow_mode_comparisons`'s schema changed twice (once to add rich statistical detail instead of a boolean flag, once to add a required provenance column), and a real, empty copy of an intermediate schema was still on disk when the second change shipped.

**Issue 2 (uncontrolled cadence):** `providers/ibm.py`'s `list_devices()` unconditionally saves a snapshot as a side effect of being called. 4 of 6 test files that exercise this correctly isolate their database writes to a temp file (mirroring the fix applied to the predictions-table incident); 2 do not, because they call the real, live IBM API (not mocked) to test other real behavior, and nobody had previously connected "this test hits the real API" with "and therefore writes a real, uncontrolled-cadence row to permanent history."

### Impact

- **Issue 1:** would have been a real, if immediately obvious, crash on the very next real `verify()` call with a known-answer claim. No real call was made before this was found; nothing user-facing was ever affected.
- **Issue 2:** no fabricated data (only real IBM backend names appear — `ibm_fez`, `ibm_kingston`, `ibm_marrakesh`). The risk is to anything that assumes a clean 6-hourly sampling cadence (e.g., drift-detection logic looking at "how many snapshots in the last 24 hours"), not to the truthfulness of any individual snapshot's values.

### Fix

**Issue 1:** dropped the empty, stale table and let the existing `CREATE TABLE IF NOT EXISTS` recreate it correctly on the next real write. Verified the new schema afterward. This was judged safe to fix without a human in the loop under a rule set specifically for this situation: an unattended fix is only in scope when the affected table is empty *and* the fix is identical to one already used and approved earlier the same night for the same table. It met both.

**Issue 2:** reported, not fixed. The natural fix (add the same `monkeypatch.setattr(ibm, "DB_PATH", ...)` isolation the other 4 test files already use, to the remaining 2) is cheap and low-risk, but is still a test-file code change, and this audit's scope for that store was explicitly report-only. Left for a human decision.

**Both issues, plus a systemic finding:** every table in both persistent stores uses the same `CREATE TABLE IF NOT EXISTS` pattern and is therefore equally exposed to the same class of drift the next time its schema changes while real data already exists — several of which (the calibration tables especially) hold thousands of real rows, meaning a future incident there would not be safe to fix unattended the way this one was. Two remediation options were proposed (a real migration mechanism using SQLite's `user_version` pragma; or a cheaper, loud startup check that refuses to proceed on any schema mismatch instead of silently deferring the failure to the first real write) — neither built yet; a human decision for tomorrow.

### What we'd do differently

The predictions-table incident's lesson was "a provenance field nobody reads is decoration." This one adds a second lesson in the same spirit: **a schema-creation statement that only handles "doesn't exist yet" is silently assuming "or is already current" — and that assumption breaks exactly when a real deployment already has real (or even just leftover-empty) state.** `CREATE TABLE IF NOT EXISTS` answers "does this table exist," not "does this table match what the code currently expects" — and this project has now hit that gap twice in one evening, on the same table, before ever auditing whether it existed anywhere else too.
