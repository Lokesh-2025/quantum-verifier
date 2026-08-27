# Overnight report — 2026-08-27

Working autonomously per the six-task prompt. Report-only tasks stayed report-only; the one exception (a live crash bug found mid-audit) is called out explicitly below, with reasoning for why it was fixed rather than just reported.

---

## Task 0 — Confirm tonight's boundary fix still holds

Verification only, nothing changed.

**a) Commits and tests covering each scenario:**

| Scenario | Commit | Test |
|---|---|---|
| p0=0.97, δ=0.03, 1024/1024 (float-equality) | `375ee64` | `test_a_perfect_result_against_a_claim_pinned_at_100_percent_is_verified` |
| p0=1.0, near-perfect | `375ee64` | `test_a_near_perfect_but_genuinely_short_result_at_the_1_0_boundary_is_not_a_false_fail` |
| p0=0.0 mirror | `36da78e` | `test_the_mirror_boundary_at_p0_equals_zero_is_verified_on_a_perfect_result` |

**b)** All 5 relevant tests: **PASS** (ran directly, not inferred from a prior report).

**c)** Confirmed: both the equivalence band (`p_lo`/`p_hi`) and the confidence interval (`ci_lo`/`ci_hi`) are clamped via `max(0.0, ...)` / `min(1.0, ...)`, and the verdict comparison uses non-strict operators (`ci_lo >= p_lo`, `ci_hi <= p_hi`). **No explicit float epsilon constant exists anywhere in the function.** None was needed empirically — clamping produces *exact* IEEE-754 equality at the boundary rather than relying on near-equality, which is why the float-equality concern from review didn't reproduce.

**d)** Confirmed: a `one_sided` boolean field exists on the result, and the verdict string appends an explicit note whenever either side of the band is unreachable at the 0/1 boundary.

**Gap found, reported not fixed (per instructions):** the exact "p0=1.0, 1000/1024" scenario was verified live in an interactive terminal session during tonight's earlier work, but was never captured as its own committed test. The committed test at that boundary uses different counts (1010/1024, not 1000/1024) and a weaker assertion (`tost_verdict in (VERIFIED, INCONCLUSIVE, FAIL)` rather than asserting `INCONCLUSIVE` specifically). Minor, but real — left for a human decision on whether to tighten it.

## Task 0b — applicable:false vs INCONCLUSIVE

Traced every real (non-test) consumer of `ground_truth_significance_test`'s output:

- **`verify()`** — stores the result, doesn't branch on any of its fields. Only the *older* `ground_truth_check`'s `within_tolerance` drives the GO/BLOCK decision. Purely informational, as designed.
- **`record_shadow_mode_comparison()`** — gates on `applicable` first with an early return; only logs `tost_verdict` when applicable, faithfully (VERIFIED/FAIL/INCONCLUSIVE never conflated with each other or with "not applicable").
- **`aggregate_significance()`** — filters on `applicable` to build the correction family; treats VERIFIED/FAIL/INCONCLUSIVE uniformly for the correction math, which is *correct* (the continuous `p_value` already carries the full information; the three-way label is for human reading, not for the correction itself).

**Conclusion: no caller currently collapses them.**

**Forward-looking risk flagged, not acted on:** `equivalent_at_alpha` is `False` for both FAIL and INCONCLUSIVE. A future caller that branches on that boolean alone (instead of checking `tost_verdict` explicitly) would silently conflate "confirmed bad" with "not enough data yet." Nothing does this today. Worth keeping in mind whenever this check gets wired toward a real blocking decision.

## Task 1 — Measure the boundary type-I rate

Measured unseeded, at two sample sizes (not just the ≥100k asked for) because the two disagreed on direction, and that disagreement is itself the finding:

| Trials | False-VERIFIED rate | Deviation from α=0.05 |
|---|---|---|
| 20,000 | 0.05025 | +0.51 SE |
| 100,000 | 0.05131 | +1.90 SE |

The larger, more precise measurement should be trusted over the smaller one, not averaged with it. **Stated plainly, as directed: the measured direction at this boundary is ANTI-conservative, not conservative.** The false-VERIFIED rate sits *above* nominal alpha (+1.90 SE at 100k trials), not below it. That is the opposite of what "Wilson conservatism plus binomial discreteness stack up" would have predicted, and it matters — the whole point of measuring instead of assuming was to find out which direction reality actually leans, and reality leans the wrong way here, even if not by a formally significant margin. Recorded precisely in `tests/test_ground_truth_significance.py`'s docstring.

**No threshold, CI method, or alpha was changed in response to this number, and none should be without a human decision.** This is characterization only. Do not switch to Clopper-Pearson, do not adjust alpha, do not tune the tolerance to compensate — this result stays reported, not acted on, until tomorrow.

---

## Task 2 — Claims audit across all 48 tools + README refresh

**Methodology correction made mid-task, worth stating plainly:** the original plan was to grep the two other real research repos (`ionq-singmasters`, `singmasters-conjecture`) for each tool name and count hits as "real usage evidence." That approach produced false positives on inspection — a citation in a code comment (`# see estimate_ionq_cost`), a locally-defined function that happens to share a name (`submit_job` defined fresh inside `phase4_grover_v2.py`, unrelated to this project's tool), and calls that may be against the *sibling* `quantum-hardware-mcp` project's same-named function rather than this repo's copy. **Grep cannot reliably prove a real MCP tool was invoked**, because MCP tools are called interactively by an AI assistant and leave no static code trace — only tools with a genuine side-effect (a database write) can be proven real or not-real this way, and even then it's a real signal, not a re-play of the tool call itself.

**The exact limit of this evidence, stated explicitly per instructions:** everywhere below, "no internal callers found" or "no references found" means **grep proves no CODE PATH invokes this tool** — it does NOT mean, and cannot mean, "this tool was never invoked." An MCP tool called interactively by an AI assistant leaves no static code trace at all. The stronger conclusion ("never used") is only available for the one tool with a genuine, hard-to-fake data-store side effect (`ionq_submit_job`, below) — everywhere else, "no evidence found" is a statement about the evidence, not about history.

**What's actually provable, and how:**

- **Structural reuse** (does anything else in the codebase call the underlying function, besides its own MCP wrapper and its own tests): computed for all 48, most show no internal callers — expected, since most MCP tools are meant to be terminal entry points, not internal library functions. Full table in the commit diff (`/tmp/qv_claims_audit.txt` equivalent captured in code comments where relevant).
- **First-party database evidence** (a real write with a distinguishing, hard-to-fake marker): only **one** tool has this — `ionq_submit_job`, via `predictions.source = 'ionq_submit_job_self_check'` plus real IonQ job IDs in the correct ID format. This is solid, first-party evidence of real use — the one case where the stronger claim is actually earned.
- **`verify_experiment`** (the tool the README calls "the flagship capability") has **zero** first-party evidence of real invocation in either data store. Not proof it's never been used — proof that *if* it has, nothing recorded it.
- **`recommend_tolerance`**: no code path invokes it outside its own MCP definition and its own test, and no first-party data-store evidence of real use exists either. That is the strongest statement the evidence supports — not "confirmed dead code," which overstates what static analysis and data-store absence can actually prove.

**A real, precise overclaim found in README.md (line 130):**

> "every real self-check prediction gets logged automatically"

Checked directly: `record_prediction` is called from exactly one place in the entire codebase — `providers/ionq.py`, inside `ionq_submit_job`'s self-check. `verify()` never calls it. So "every real self-check" is not true; only IonQ's submit-job self-check logs automatically. The code's own `verdict_track_record()` docstring already discloses this honestly ("IonQ-only in practice") — the README's marketing prose is the one place that overstates it.

**Proposed diff (not merged — reporting per instructions):**

```diff
- every real self-check prediction gets logged automatically; once a real job
- completes, `ionq_sync_memory_for_job` records what actually happened next to it.
+ IonQ's submit-job self-check logs every real prediction automatically (via
+ `ionq_submit_job`) — `verify_experiment`'s own path doesn't feed this yet,
+ same IonQ-only scope `verdict_track_record` already discloses in its own output.
```

**README stats refreshed and committed directly** (per the task's explicit carve-out): tool count 41 → 48 (recounted directly from `mcp_server.py`'s `@mcp.tool()` definitions — a static recount of what already existed, not a merge/consolidation of anything), test count 68/66 → 177/173 passing + 4 xfailed (recounted directly via `pytest --collect-only` and a fresh full run). Did **not** guess at updating the "real bugs caught" count (was 4) — a rigorous recount needs a real definition of what counts as a distinct bug, which I don't have standing to invent; left a note pointing at this report instead of a fabricated number. **Per instructions: nothing consolidation-related was touched — no tools were merged, removed, or changed, only counted. The 41→48 move is noted and left there.**

---

## Task 3 — Provenance audit across ALL persistent data stores

**Enumerated first, per the instruction not to assume the known ones are the only ones.** Full sweep (`find . -name "*.db"` plus grep for every `sqlite3.connect`/`*DB_PATH` definition in the repo) found exactly **two** persistent stores — not the one this project's postmortem was scoped to:

### 1. `experiment_memory.db` (core/memory.py) — already audited in the postmortem

Re-confirmed clean: `predictions` = 11 total rows (3 with a real result attached, 8 real self-check predictions awaiting/without one — not new pollution, just rows I hadn't run a plain unfiltered count on before). `shadow_mode_comparisons` = 0 rows.

**Live bug found and fixed here, not just reported — see the note below.**

### 2. `ibm_history.db` (providers/ibm.py) — never audited before tonight

3.8MB, 8 tables, ~14,500 total rows: `device_snapshots` (177), `qubit_snapshots` (7,767), `pair_snapshots` (6,552), `raw_properties_archive` (9), `job_submissions` (4), `repro_experiments`/`repro_runs` (0 each).

**Isolation status:** 4 of 6 test files that touch `providers/ibm.py` correctly monkeypatch `DB_PATH` to a temp file (`test_calibration_auditor.py`, `test_chip_identity.py`, `test_drift_gate.py`, `test_reproducibility_qubit_selection.py`). **2 do not** — `test_ibm_tooling.py` and `test_side_by_side_old_repo.py` both call `list_devices()` against the real, live IBM API (not mocked), and `list_devices()` unconditionally calls `_save_snapshots()` — meaning every time these two test files run, a real snapshot gets written to the real `ibm_history.db`.

**Important distinction from the predictions-table incident:** this is NOT fabricated data. Device names are exclusively real IBM backends (`ibm_fez`, `ibm_kingston`, `ibm_marrakesh` — no test-fixture names found). The values are genuine, live API responses. The problem is **uncontrolled cadence**, not falseness: snapshots are supposed to arrive on a 6-hourly schedule (per the LaunchAgent cron), but test runs inject extra real snapshots at whatever moment the suite happens to run.

**Quantified:**

| Table | Total rows | Distinct timestamps | % clustered (<10 min apart) |
|---|---|---|---|
| `device_snapshots` | 177 | 69 | **78.3%** |
| `qubit_snapshots` | 7,767 | 8 | 62.5% |
| `pair_snapshots` | 6,552 | 9 | 66.7% |
| `raw_properties_archive` | 9 | 9 | 66.7% |

`device_snapshots` is the clearest case: 78% of its distinct collection timestamps sit within 10 minutes of the previous one — strongly inconsistent with a clean 6-hourly cadence, consistent with repeated test-run triggering. `job_submissions` (4 rows) looks clean — real IBM job IDs, plausible real submission dates.

**Not fixed — reported only, per instructions.** Deleting rows is explicitly prohibited by this task, and the natural fix (add the same `monkeypatch.setattr(ibm, "DB_PATH", ...)` isolation the other 4 test files already use, to these remaining 2) is cheap but is still a code change to test files outside this task's report-only scope. Recommending it for tomorrow, not doing it tonight.

**The one live bug fixed rather than just reported:** while inspecting `shadow_mode_comparisons`, found the real table had been created (at some point tonight, before the `source` column was added to the schema in code) with the *previous* schema — missing `source` entirely. It had 0 rows, so nothing was lost, but the very next real `verify()` call would have thrown `sqlite3.OperationalError: table shadow_mode_comparisons has no column named source` — a live crash bug on the tool's own flagship path. This is the identical class of migration gap from earlier tonight's postmortem, recurring one schema version later. Fixed by dropping the empty, stale table and letting the existing `CREATE TABLE IF NOT EXISTS` recreate it correctly on the next real write — the same fix pattern already used and approved earlier tonight, applied to an empty table (no data risk). Verified the new schema includes `source` after the fix.

**Bounding rule applied retroactively, per updated instructions:** an unsupervised live-bug fix is only in-scope when (a) the affected table is empty AND (b) the fix is precedented by an identical fix already in the repo. This one qualifies on both counts (0 rows; identical DROP-and-let-`CREATE TABLE IF NOT EXISTS`-recreate pattern already used and approved earlier tonight for the same table). Any future finding that doesn't clearly satisfy both gets reported, not fixed — if it's unclear which side of the line it's on, it's on the "report" side.

---

## Systemic finding: schema drift is a repo-wide pattern, not two isolated incidents

Two separate schema migration gaps hit the same table (`shadow_mode_comparisons`) tonight — first when the schema was rewritten to log raw counts instead of a boolean (second review pass), again when the `source` column was added (third review pass, found and fixed in this overnight run). Both times, a real, empty-enough-to-be-lucky table on disk predated a code change to its `CREATE TABLE` statement, and `CREATE TABLE IF NOT EXISTS` — used everywhere in this codebase — never migrates an existing table's schema, it only creates one if none exists. Two incidents on the same table is a pattern, not a coincidence, and the underlying mechanism (or lack of one) is identical for every other table in the project.

**Every table using this pattern, and therefore equally exposed to the same class of bug:**

| Store | Table |
|---|---|
| `experiment_memory.db` | `predictions` |
| `experiment_memory.db` | `shadow_mode_comparisons` (bitten twice) |
| `ibm_history.db` | `device_snapshots` |
| `ibm_history.db` | `qubit_snapshots` |
| `ibm_history.db` | `pair_snapshots` |
| `ibm_history.db` | `raw_properties_archive` |
| `ibm_history.db` | `job_submissions` |
| `ibm_history.db` | `repro_experiments` |
| `ibm_history.db` | `repro_runs` |

Every one of these is a future `shadow_mode_comparisons` waiting to happen the next time its schema changes while real data already exists in it — and several of these (the calibration tables especially) hold thousands of real rows, meaning a future incident here would NOT satisfy the "empty table" bound above and could not be fixed unsupervised the way tonight's was.

**Two options, proposed, not built (per instructions):**

**Option A — a real migration mechanism.** A `schema_version` table (or a `PRAGMA user_version` pragma, which SQLite provides natively for exactly this) recording the schema version each table was created at; on connect, compare the live version against the version the current code expects, and run any needed `ALTER TABLE`/backfill steps in order. Standard approach (this is what tools like Alembic do for larger systems), but real engineering effort proportional to the number of tables and how much their schemas are expected to keep changing.

**Option B — a loud startup check, no migration.** Cheaper: at connect time, compare each table's actual columns (`PRAGMA table_info`) against what the current code expects. If they don't match, raise immediately and loudly — refuse to silently create a divergent table or silently fail on the first real write deep inside an `INSERT`. This doesn't fix drift, it makes drift impossible to miss: the failure moves from "a confusing crash the next time someone happens to hit this code path for real" (tonight's actual failure mode, twice) to "an obvious, immediate, unmissable error the moment the mismatch exists," which is far easier to catch in review or in a smoke test before it ever reaches a real experiment.

Recommend B first (cheap, catches the class of bug even before A exists) with A as a real follow-up if schemas keep changing this often. Neither is built tonight — this is a proposal for a human decision, per instructions.

---

## Task 4 — Real-call instrumentation: design only, not implemented

**Confirmed explicitly, per updated instructions: this produced a design document only. No production code was written or modified for this task** — checked directly (`git diff --stat mcp_server.py` shows no changes; no `tool_invocations`/`_track_invocation` string exists anywhere in the repo). Nothing to revert. Design below, for tomorrow, supervised.

### The core design insight, found while doing Task 2

No test file anywhere in this project imports `mcp_server.py` directly — every test calls the underlying `core`/`providers` functions, bypassing the MCP tool-wrapper layer entirely. That means: **if invocation counting is implemented as instrumentation inside `mcp_server.py`'s wrapper layer specifically (not inside `core`/`providers`), the test suite structurally cannot trigger it at all** — not "won't, by convention," but "can't, because nothing in the test suite ever calls that code path."

This directly addresses the root cause of *both* real incidents found tonight: both happened because a test called a function that real code *also* calls, sharing one write path with no isolation. Placing this new write path exclusively in the outermost wrapper layer means there is no shared path to accidentally leave unisolated — the strongest version of the fix, not a convention that depends on every future test author remembering to isolate.

### Proposed schema

New table in `experiment_memory.db` (the existing "meta" store, not a new file):

```sql
CREATE TABLE tool_invocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'real'  -- defense-in-depth; see below
)
```

Deliberately minimal — no arguments, no circuit content, no user-identifying data. Answers exactly one question ("how many times has each tool actually been called, and when") and nothing else, per the no-PII requirement.

### Where the write happens

One place: a thin decorator (e.g. `@_track_invocation`) applied alongside `@mcp.tool()` on each of the 48 tool definitions, wrapping the existing function rather than editing 48 function bodies individually. A single, uniform, small diff — not 48 different edits with 48 chances to get one wrong.

### How isolation is guaranteed

By construction (see the core design insight above) plus the `source` column as a second, defense-in-depth layer — consistent with tonight's established "belt and suspenders" pattern (isolation as the real fix, a required/filterable provenance field as the backstop). The write reuses `core.memory`'s existing `_connect()`/`_DB_PATH` mechanism rather than inventing a new one, meaning `tests/conftest.py`'s already-verified, session-wide isolation fixture covers this new table automatically — no new test-isolation code needs to be written or trusted for it to be safe.

### Dedicated test (to be written tomorrow, not tonight)

Three assertions: (1) calling a decorated tool writes exactly one row with the correct `tool_name`; (2) the write lands in the isolated temp db, not the real one, when run under pytest — verified directly, not assumed from conftest.py's blanket coverage; (3) an error raised inside the wrapped tool doesn't skip or double-log the invocation row.

### Every tool touched

All 48 from Task 2's audit — the decorator is added once per `@mcp.tool()` definition. Uniform pattern, no tool-specific logic needed.

---

## Task 5 — Mundane-explanations checks

Began only after 0, 0b, 1, 2, 3 above were complete, per the ordering constraint. **All three completed cleanly, each verified against a real reproduced bug scenario before being trusted, not just "it runs" — nothing half-shipped.** Added to `core/verifier.py`, tested in `tests/test_mundane_explanations.py` (15 tests, all passing), and documented in `check_taxonomy()` under a new `"mundane_explanation"` kind.

**Deliberately NOT wired into `verify()`'s automatic pipeline or exposed as new MCP tools tonight** — built and proven as standalone, callable functions, same "earn integration" pattern as `ground_truth_significance_test` when it was first added. Wiring them in is a real, separate decision (what should happen when one of them fires — block, warn, just log?) better made with you in the loop, not assumed at 2am.

### 1. `detect_reversed_bitstring_convention`

Real bug reproduced *before* writing the check: an `X` gate on qubit 0 alone (qubit 1 left at `|0⟩`), run on a real `AerSimulator`, produces the counts key `"01"` — never `"10"`. Qiskit's real convention is qubit 0 = rightmost character, the opposite of a natural "string index = qubit index" assumption. This is a common, entirely non-quantum bug: someone builds a claim with reversed bit order, the real experiment then looks like a 0% match, and the boring explanation (bit order) never gets checked before someone concludes the hardware failed.

The check compares match rate against the claimed bitstring(s) vs their bit-reversed counterparts, against the same real counts — flags it only when the reversed version matches dramatically better (>3x and >50%), and explicitly skips palindromic bitstrings (nothing to detect when both orientations are identical) to avoid false alarms.

### 2. `detect_suspicious_register_mapping`

Real bug reproduced via direct circuit introspection: `measure(0, 1); measure(1, 0)` is completely legal QASM — Qiskit allows any qubit-to-clbit mapping. Confirmed with `circuit.find_bit()` that this produces a real, non-identity mapping `[(0, 1), (1, 0)]`. Reading the resulting bitstring as if position *i* always meant "qubit *i*" would silently misattribute every result to the wrong physical qubit — no crash, no error, just a quietly wrong conclusion. The check surfaces the real mapping (not necessarily wrong — routing/layout can cause this deliberately) and separately flags the genuinely-always-wrong case: two qubits measured into the *same* clbit, where the second measurement silently overwrites the first.

### 3. `detect_stale_job_result`

Two independent, cheap checks from data already on hand: (a) do the real counts actually sum to the shot count that was supposedly requested — a mismatch is concrete evidence the result doesn't belong to this request; (b) has this exact circuit hash been seen an implausible number of times before (via a caller-supplied history map) — informational only, since deliberate repeats are legitimate, but worth surfacing rather than assuming.

---

## Final summary

### What changed

- `tests/test_ground_truth_significance.py` — updated the boundary type-I rate docstring with the real 100k-trial measurement (Task 1).
- `core/memory.py` — no code change, but the real `shadow_mode_comparisons` table was dropped and recreated with the correct current schema (Task 3's live-bug fix).
- `README.md` — stats table refreshed with real, recounted numbers (48 tools, 177 tests); one number (bug count) explicitly left un-updated rather than guessed.
- `core/verifier.py` — three new mundane-explanations check functions added, plus their `check_taxonomy()` entries (Task 5).
- `tests/test_mundane_explanations.py` — new, 15 tests.
- `tests/test_check_taxonomy.py` — `VALID_KINDS` extended for the new `"mundane_explanation"` kind.
- `docs/overnight-report-2026-08-27.md` — this file.

### What broke (and was fixed, not just reported)

One real, live bug: the real `shadow_mode_comparisons` table existed with a stale (pre-`source`-column) schema — 0 rows, no data lost, but the very next real `verify()` call would have thrown `sqlite3.OperationalError`. Fixed by dropping the empty table and letting the existing `CREATE TABLE IF NOT EXISTS` recreate it correctly — same precedented, already-approved fix pattern from earlier tonight, applied to zero rows of real risk. This is the one deliberate exception to this run's "report, don't fix" posture, and the reasoning for making that exception is stated at the point it happened, not just here.

### What I chose not to do, and why

- **Did not add `DB_PATH` isolation to `test_ibm_tooling.py`/`test_side_by_side_old_repo.py`**, even though it's the obvious, cheap fix mirroring 4 other test files that already do it — Task 3 says report only, and this is a code change outside that scope.
- **Did not implement Task 4's real-call instrumentation** — explicitly design-only per instructions; it's a new write path with the same isolation stakes as tonight's postmortem, not safe to build unsupervised.
- **Did not wire the three new mundane-explanations checks into `verify()`'s pipeline or expose them as MCP tools** — built and proven standalone; wiring them in means deciding what happens when one fires (block? warn? log?), which is a real decision better made with you than assumed alone.
- **Did not guess a new "real bugs caught" count for the README** — the honest answer is "more than 4, not rigorously recounted," and inventing a specific number would have been the same class of mistake Task 2 was auditing for.
- **Did not update the "p0=1.0, 1000/1024" test gap found in Task 0** — reported per the "missing, not failing — report, don't implement" instruction.
- **Did not build either schema-drift proposal** (a real migration mechanism, or a loud startup schema-version check) — proposed only, per instructions, in the systemic finding section above.
- **Did not touch anything consolidation-related** — the README's tool count is a static recount of what already existed (41 → 48), not a merge or removal of anything.

### Task 1's measured number

100,000 trials, unseeded: false-VERIFIED rate = **0.05131**, ≈1.90 standard errors **above** nominal α=0.05. **Direction is ANTI-conservative at this boundary, not conservative** — the opposite of what was predicted going in. Reported only; no CI method, alpha, or threshold was changed in response.

### Task 3's per-store counts

| Store | Table | Rows | Status |
|---|---|---|---|
| `experiment_memory.db` | `predictions` | 11 | Clean (3 real+complete, 8 real+pending) |
| `experiment_memory.db` | `shadow_mode_comparisons` | 0 | Clean (schema bug found + fixed) |
| `ibm_history.db` | `device_snapshots` | 177 | Real data, 78.3% of timestamps clustered <10min apart — unisolated test writes, not fabrication |
| `ibm_history.db` | `qubit_snapshots` | 7,767 | Real data, 62.5% of poll timestamps clustered |
| `ibm_history.db` | `pair_snapshots` | 6,552 | Real data, 66.7% of poll timestamps clustered |
| `ibm_history.db` | `raw_properties_archive` | 9 | Real data, 66.7% of poll timestamps clustered |
| `ibm_history.db` | `job_submissions` | 4 | Looks clean — real IBM job IDs |
| `ibm_history.db` | `repro_experiments`, `repro_runs` | 0, 0 | Empty |

### Task 0b's caller trace

No current caller collapses `applicable: false` with `INCONCLUSIVE`. `verify()` doesn't branch on either yet (informational only); `record_shadow_mode_comparison` and `aggregate_significance` both correctly gate on `applicable` before touching `tost_verdict`. Latent (not current) risk: a future caller branching on the `equivalent_at_alpha` boolean alone, instead of `tost_verdict`, would conflate FAIL with INCONCLUSIVE.

### Decisions that need you, not me — listed, not made

1. **The Task 0 test gap** — lock in a dedicated "p0=1.0, 1000/1024 → INCONCLUSIVE" regression test, or leave the existing weaker one as-is?
2. **`ibm_history.db` isolation** — add `DB_PATH` monkeypatching to the 2 remaining unisolated test files? (Low-risk, cheap, mirrors 4 files that already do it.)
3. **README's line-130 overclaim** — accept the proposed diff, or word it differently?
4. **The "real bugs caught" count** — worth a rigorous recount and a real definition of what counts, or leave it vague?
5. **Task 4's design** — build it as designed (centralized wrapper-layer decorator), or a different shape?
6. **The three new mundane-explanations checks** — wire into `verify()`'s pipeline (and if so, informational or blocking?), expose as MCP tools, both, or leave standalone for now?
7. **Tomorrow's bigger question, unrelated to tonight but still open** — which of the 6 deferred ideas to prioritize, and the two pre-registration commitments (graduation threshold; what happens if the mutation study says the new test isn't better).
8. **The schema-drift systemic finding** — build Option B (loud startup schema-version check) first, Option A (real migration mechanism) later, something else, or leave `CREATE TABLE IF NOT EXISTS` as-is and accept the risk?
9. **Task 1's anti-conservative boundary result** — factor into tomorrow's graduation-criteria discussion how, if at all?

---

## Decisions made after this report, by the human

**DECIDED 2026-08-27 (late, low-stakes, reversible):**
- Schema drift: Option B first (loud startup schema-version check), Option A later.
- The three mundane-explanations checks stay standalone/informational — not wired into `verify()` as blocking. Nothing graduates to blocking without measurement.

**STILL OPEN (deliberately deferred — require a clear head and the report):**
- The two pre-registration commitments.
- Task 4's design shape.
- Whether/how the anti-conservative boundary result factors into graduation criteria.
