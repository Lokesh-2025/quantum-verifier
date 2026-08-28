# Scoping note: IBM prediction-tracking parity with IonQ

Date: 2026-08-28. Deliberately a scoping note, not a build — captures the real shape of the gap so the next session starts from what's already known instead of re-discovering it. No solution designed here on purpose.

## What was asked for

Give IBM the same real prediction-vs-reality logging IonQ already has (`record_prediction` at submission time, `record_real_result` once a real result comes back), so `recommend_tolerance`/`memory_summary`/`verdict_track_record` stop being IonQ-only in practice.

## What was actually found, reading the real code (not assumed)

**IonQ's real structure** (`providers/ionq.py::ionq_submit_job`): takes `expected_marked_bitstrings`/`expected_amplification` as real parameters, runs a pre-submission simulation to compute a predicted value, compares it against the claim as a self-check (refusing to submit on a bad self-check), and — only on the real-hardware path — calls `record_prediction(...)` with that predicted value before ever submitting. After a real job completes, a **separate** function, `ionq_sync_memory_for_job(job_id)`, fetches real results via IonQ's REST API, computes the real observed amplification per circuit, and calls `record_real_result(...)` against the matching prediction.

**IBM's real structure** (`providers/ibm.py::submit_job`, checked directly, not assumed to mirror IonQ):

- **`submit_job` has no self-check step and no `expected_marked_bitstrings`/`expected_amplification` parameters at all.** It parses the circuit, checks for a recent drift alert, checks quota, transpiles, and submits — nothing in that path ever computes or tracks a "predicted" value, because it's never been asked to track a claim in the first place.
- **`job_results` returns raw counts (or an expectation value for Estimator-mode jobs), with zero connection to any prediction record.** There's no `find_predictions_for_job`/`record_real_result` call anywhere in it, and no separate sync function exists — **`ionq_sync_memory_for_job` has no IBM equivalent at all**, not even an unused or partial one.

**The predictions table itself needs no schema change** — `provider` is a plain `TEXT NOT NULL` column, not an enum restricted to `"ionq"`. Confirmed directly, not assumed.

## What this means for scope

This is not "wire two existing hooks the way IonQ does it" — the hooks don't exist on the IBM side. Building real parity means:

1. **Adding new parameters to `submit_job`** (`expected_marked_bitstrings`, `expected_amplification`, presumably a tolerance too) — a real public API change to an existing, already-used tool, not an internal-only addition. Needs a decision on whether IBM's version gets a self-check gate like IonQ's (refuse to submit on a bad self-check) or just records the claim without gating — those are different product decisions, not just implementation details.
2. **Writing a new function from scratch** — an IBM-side equivalent of `ionq_sync_memory_for_job` — adapted to IBM's actual results shape, which is structurally different from IonQ's: `job_results` returns `counts_by_register` (a dict keyed by classical register name, built from `pub_result.data`'s bit arrays) rather than IonQ's REST histogram keyed by integer outcome. The real-amplification computation logic from IonQ's sync function can't be copied as-is; it needs to be re-derived against this shape.
3. **Deciding where `record_prediction` actually fires** — IonQ's fires inside the self-check loop, before the real-hardware gate. IBM's `submit_job` has no equivalent pre-submission check to hook into, so this needs a real design decision, not just a mirrored call site.

## Not decided here

- Whether IBM's version should gate submission on a self-check (like IonQ) or just log the claim.
- The exact shape of the new `submit_job` parameters.
- How to structure the new sync function, or whether it should be called automatically vs. manually (IonQ's is manual, called once a job is known to be complete).

All of that is real design work for whoever picks this up next — this note exists so that work starts from "here's exactly what's different and why" instead of re-reading both provider files from scratch first.
