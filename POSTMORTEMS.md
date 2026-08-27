# Postmortems

Real incidents in this project's own tooling, written up the way we'd want any quantum result written up: what happened, how it was actually found, what it did and didn't affect, and what changed so it can't happen the same way twice. Nothing here is hypothetical — every number below came from querying the real database, not from memory or assumption.

---

## 2026-08-27 — The tool's own "how trustworthy am I" number was 93% fake

**Severity:** Data integrity. No real experiment decision was affected, but the metric that exists specifically to answer "should you trust this tool" was quietly wrong for two weeks.

**Status:** Fixed. Data cleaned, root cause closed, defense-in-depth added, regression tests added.

### Summary

`verdict_track_record()` — the function whose entire job is to answer "when this tool's verdict would have said GO, how often was that actually true?" using real recorded prediction-vs-reality pairs — was reporting `overall_n: 45, overall_hit_rate: 1.0`. Of those 45 rows, **42 were not real data**. They were an identical, repeated test fixture (`predicted_amplification: 10.0, real_amplification: 8.0`) inserted by `tests/test_intelligence.py` every time the test suite ran, because that test called the same database-writing functions (`record_prediction`, `record_real_result`) the real tool uses, with no isolation from the production database. Only 3 of the 45 rows — the ones from a real IonQ hardware job — were genuine.

The reported hit rate (1.0) didn't happen to change once the fake data was removed, because the synthetic pair was deliberately constructed to read as a "hit." That's not reassuring — it means the number was accidentally right, not honestly right. A tool whose whole premise is distinguishing real signal from noise had, in its own self-assessment, been doing exactly the kind of unvalidated aggregation it exists to catch other people doing.

### How it was found

Not by design. An external review of an unrelated statistical rewrite (see the git history around 2026-08-27) suggested auditing the project's data logs for provenance after a smaller, structurally identical bug was found and fixed in a different, newer table the same day. Applying that same audit — "who actually wrote every row in this table, and can you prove it" — to the older `predictions` table (in production since 2026-08-13) surfaced this.

### Root cause

```python
# tests/test_intelligence.py, before the fix
for i in range(MIN_DATA_POINTS_FOR_RECOMMENDATION):
    pred = record_prediction(..., source="unit_test")
    record_real_result(pred["prediction_id"], real_amplification=8.0)
```

`record_prediction`/`record_real_result` write to whatever `core.memory._DB_PATH` currently points at. In every test run before this fix, that was the real `experiment_memory.db` — the same file used by `verdict_track_record()`, `memory_summary()`, and `recommend_tolerance()`. The table already had a `source` column (present since the table was created), correctly tagging these rows as `"unit_test"` — but nothing ever *read* that column. Provenance was tracked from day one and never enforced.

### Impact

- **Corrupted:** `verdict_track_record()` and `memory_summary()`'s live output — both drew from a pool that was 93% synthetic (42/45 rows).
- **Not corrupted:** `verify()`'s actual GO/BLOCK decisions. Neither function is consulted inside `verify()`'s decision path — it evaluates each experiment against its own current data, not against the historical trust record. No real verification verdict was ever influenced by this.
- **Not corrupted:** the `shadow_mode_comparisons` table (the equivalence-test agreement log added earlier the same day) — a related but separate incident, caught and fixed independently a few hours earlier, which is actually what prompted auditing this older table too.
- **Real data lost:** none. All 3 genuine rows (from job `019ff976-2728-7726-8839-9109c9122b98`) were preserved; only the 42 confirmed-synthetic rows were removed.

### Before / after

```
BEFORE  overall_n: 45   overall_hit_rate: 1.0   (42 of 45 rows fake)
AFTER   overall_n: 3    overall_hit_rate: 1.0   (all 3 rows real)
```

### Fix

1. **Data**: deleted the 42 rows matching `source = 'unit_test'`, after confirming by inspection that every one of them was the identical repeated fixture value.
2. **Root cause**: added `tests/conftest.py` — a session-wide, autouse fixture that redirects `core.memory._DB_PATH` to an isolated temp file for the *entire* test run, so no test, now or in the future, can write to the real database just by calling a function that happens to log to it.
3. **Defense in depth**: added `_NON_REAL_SOURCES` filtering to `memory_summary()` and `verdict_track_record()` so a known-synthetic source can never again be silently averaged into the real number, even if isolation is somehow bypassed later. Added the same required, non-nullable `source` column to `shadow_mode_comparisons`.
4. **Test hygiene**: `test_intelligence.py` no longer needs to disguise its fixture data — now that it runs against a properly isolated database, it uses the real default source label honestly, and the filter doesn't need to special-case it.
5. **Regression tests**: added tests asserting a known-synthetic source is excluded from both functions' output, and that a mix of real and synthetic data only counts the real rows.

### What we'd do differently

The `source` column existed from the start — this wasn't a missing idea, it was an unenforced one. The actual lesson isn't "add provenance tracking," it's: **a provenance field that nothing reads is decoration, not a guarantee.** Every table in this project that answers a trust question now needs an explicit filter test proving synthetic data is excluded, not just a column that could theoretically support one.
