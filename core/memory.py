"""
Experiment Memory — the layer deferred since Phase 1's original plan,
explicitly held back until there was real prediction-vs-reality data
worth learning from. There is now: the E1 real hardware run
(job 019ff976-2728-7726-8839-9109c9122b98) gave three real data points
where a free-simulator prediction can be checked against what actually
happened on real IonQ hardware.

This does the simplest useful thing first: record every prediction made
(via verify_experiment or ionq_submit_job's self-check) and let a real
result be attached later, then answer the one question that actually
matters — "how much should I trust this tool's predictions, and does
that trust vary by provider or device?" — with real numbers, not a guess.

Deliberately NOT built yet (a real scope boundary, not an oversight):
automatic postmortem explanation of *why* a prediction was wrong, and any
kind of recommendation/intelligence layer on top of this data. This is
the raw data layer those would need — building them before this existed
would have been building on nothing.
"""
import hashlib
import json
import os
import sqlite3

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "experiment_memory.db")


def _connect():
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            provider TEXT NOT NULL,
            target_device TEXT NOT NULL,
            circuit_hash TEXT NOT NULL,
            circuit_index_in_job INTEGER,
            marked_bitstrings TEXT,
            predicted_amplification REAL,
            source TEXT NOT NULL,
            real_job_id TEXT,
            real_amplification REAL,
            real_result_recorded_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_mode_comparisons (
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
    """)
    conn.commit()
    return conn


def _circuit_hash(qasm_string: str) -> str:
    return hashlib.sha256(qasm_string.encode()).hexdigest()[:16]


def record_prediction(qasm_string: str, provider: str, target_device: str,
                       predicted_amplification: float = None, marked_bitstrings: list = None,
                       source: str = "verify_experiment", circuit_index_in_job: int = None) -> dict:
    """Log a prediction at the moment it's made — call this from anywhere
    a self-check or verify_experiment call produces a predicted
    amplification, so it's on record before real results exist to compare
    against (avoids ever retroactively cherry-picking which predictions
    to remember). circuit_index_in_job matters for batched jobs, where a
    real job's results come back as one entry per circuit in submission
    order — needed later to match this prediction to the right one."""
    import datetime
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO predictions (timestamp, provider, target_device, circuit_hash, "
        "circuit_index_in_job, marked_bitstrings, predicted_amplification, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (datetime.datetime.now(datetime.timezone.utc).isoformat(), provider, target_device,
         _circuit_hash(qasm_string), circuit_index_in_job,
         json.dumps(marked_bitstrings) if marked_bitstrings else None,
         predicted_amplification, source),
    )
    conn.commit()
    prediction_id = cur.lastrowid
    conn.close()
    return {"prediction_id": prediction_id, "recorded": True}


def attach_job_id(prediction_id: int, job_id: str) -> dict:
    """Link a prediction to the real job it was submitted as, at
    submission time — before the real result is known. Lets a later,
    separate step find and complete this record once the job finishes."""
    conn = _connect()
    cur = conn.execute("UPDATE predictions SET real_job_id = ? WHERE id = ?", (job_id, prediction_id))
    conn.commit()
    updated = cur.rowcount
    conn.close()
    if updated == 0:
        return {"error": f"no prediction found with id {prediction_id}"}
    return {"prediction_id": prediction_id, "job_id": job_id, "linked": True}


def record_real_result(prediction_id: int, real_amplification: float, real_job_id: str = None) -> dict:
    """Attach a real hardware result to a previously-recorded prediction,
    once the real job actually completes."""
    import datetime
    conn = _connect()
    cur = conn.execute(
        "UPDATE predictions SET real_amplification = ?, real_job_id = ?, real_result_recorded_at = ? "
        "WHERE id = ?",
        (real_amplification, real_job_id, datetime.datetime.now(datetime.timezone.utc).isoformat(), prediction_id),
    )
    conn.commit()
    updated = cur.rowcount
    conn.close()
    if updated == 0:
        return {"error": f"no prediction found with id {prediction_id}"}
    return {"prediction_id": prediction_id, "real_result_recorded": True}


def find_predictions_for_job(job_id: str) -> list:
    """All predictions linked to a given real job ID, ordered by circuit
    index — used to match a batch job's real per-circuit results back to
    the right predictions once results are available."""
    conn = _connect()
    rows = conn.execute(
        "SELECT id, circuit_index_in_job, marked_bitstrings, predicted_amplification "
        "FROM predictions WHERE real_job_id = ? ORDER BY circuit_index_in_job",
        (job_id,),
    ).fetchall()
    conn.close()
    return [
        {"prediction_id": r[0], "circuit_index_in_job": r[1],
         "marked_bitstrings": json.loads(r[2]) if r[2] else None, "predicted_amplification": r[3]}
        for r in rows
    ]


def memory_summary(provider: str = None) -> dict:
    """How much should predictions from this tool actually be trusted?
    Real numbers from real recorded predictions, broken down by provider
    and target device, not a guess."""
    conn = _connect()
    query = "SELECT provider, target_device, predicted_amplification, real_amplification FROM predictions WHERE real_amplification IS NOT NULL"
    params = ()
    if provider:
        query += " AND provider = ?"
        params = (provider,)
    rows = conn.execute(query, params).fetchall()
    total_predictions = conn.execute(
        "SELECT COUNT(*) FROM predictions" + (" WHERE provider = ?" if provider else ""), params
    ).fetchone()[0]
    conn.close()

    if not rows:
        return {
            "total_predictions_ever_recorded": total_predictions,
            "predictions_with_real_results": 0,
            "note": "No predictions have a real result attached yet. Call record_real_result "
                    "once a real job completes to start building actual trust data.",
        }

    by_device = {}
    for prov, device, predicted, real in rows:
        key = f"{prov}/{device}"
        by_device.setdefault(key, []).append((predicted, real))

    breakdown = {}
    for key, pairs in by_device.items():
        errors = [abs(p - r) / r for p, r in pairs if r and p is not None]
        breakdown[key] = {
            "n": len(pairs),
            "mean_relative_error": round(sum(errors) / len(errors), 3) if errors else None,
            "predictions_vs_real": [{"predicted": p, "real": r} for p, r in pairs],
        }

    return {
        "total_predictions_ever_recorded": total_predictions,
        "predictions_with_real_results": len(rows),
        "by_provider_device": breakdown,
        "note": "mean_relative_error is |predicted - real| / real, averaged — lower means "
                "this tool's predictions have been more trustworthy for that provider/device so far. "
                "Small sample sizes should be read with real caution, not treated as settled.",
    }


def verdict_track_record(tolerance: float = 0.5, provider: str = None) -> dict:
    """
    Added 2026-08-24. memory_summary() answers "how far off were my
    predictions" (continuous error). This answers the more direct,
    real-world question: "when this tool's verdict would have said GO,
    how often was that actually right?" — a real, honest hit rate from
    real recorded prediction-vs-reality pairs, not a claimed one.

    A prediction "would have been GO" if the real result actually landed
    within `tolerance` of the prediction (same relative-tolerance logic
    core.verifier.ground_truth_check uses at verification time — pass the
    same tolerance you actually verify with for an apples-to-apples number).

    NOTE: only providers/ionq.py currently logs predictions here
    automatically (ionq_submit_job / ionq_job_results). IBM's submit_job
    does not yet — so this track record is IonQ-only in practice until
    that's added, and this function says so explicitly rather than
    silently reporting a partial number as if it were complete.
    """
    conn = _connect()
    query = ("SELECT provider, target_device, predicted_amplification, real_amplification "
              "FROM predictions WHERE real_amplification IS NOT NULL AND predicted_amplification IS NOT NULL")
    params = ()
    if provider:
        query += " AND provider = ?"
        params = (provider,)
    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        return {
            "n": 0,
            "note": "No predictions with both a predicted and real amplification recorded yet.",
        }

    by_device = {}
    for prov, device, predicted, real in rows:
        key = f"{prov}/{device}"
        by_device.setdefault(key, []).append((predicted, real))

    def _hit_rate(pairs):
        hits = 0
        for predicted, real in pairs:
            lo, hi = predicted * (1 - tolerance), predicted * (1 + tolerance)
            if lo <= real <= hi:
                hits += 1
        return hits, len(pairs)

    breakdown = {}
    total_hits, total_n = 0, 0
    for key, pairs in by_device.items():
        hits, n = _hit_rate(pairs)
        total_hits += hits
        total_n += n
        breakdown[key] = {"n": n, "hits": hits, "hit_rate": round(hits / n, 3) if n else None}

    return {
        "tolerance_used": tolerance,
        "overall_n": total_n,
        "overall_hit_rate": round(total_hits / total_n, 3) if total_n else None,
        "by_provider_device": breakdown,
        "scope_caveat": "Only providers/ionq.py logs predictions here automatically as of "
                         "2026-08-24 — IBM's submit_job does not yet, so this is an IonQ-only "
                         "track record in practice, not a full-tool one. Treat it as that.",
        "note": f"A 'hit' means the real result landed within {int(tolerance*100)}% of the "
                "prediction — the same logic that would have produced a GO verdict at "
                "verification time. This is the tool's own real accuracy record, not a claim.",
    }


def record_shadow_mode_comparison(provider: str, target_device: str, qasm_string: str,
                                   old_check: dict, new_check: dict,
                                   old_check_tolerance: float = None) -> None:
    """
    Added 2026-08-27, per external review's item 4 — called the "cheapest
    thing on the list and probably the most informative." Every time
    verify() runs both ground_truth_check (the old tolerance-band
    heuristic) and ground_truth_significance_test (the real TOST
    equivalence test), log whether they agreed, for free, before either
    one is trusted to block on its own. Doesn't need a real hardware
    result to be useful — it's comparing two checks against each other,
    both already computed from the same simulated/hardware-aware result.

    SCHEMA REWRITTEN 2026-08-27 (second review pass) after a real gap was
    caught before any real data existed to lose: the first version logged
    only a boolean agree/disagree flag per comparison. That makes it
    impossible to ever recompute a verdict under a different alpha, a
    different tolerance margin, or a different CI method later — exactly
    the kind of re-analysis this log exists to eventually support. Now
    logs the raw counts (total_shots, marked_shots) and every real
    statistical quantity (claimed probability, equivalence margin, CI
    bounds/method, p-value, TOST verdict) needed to recompute the verdict
    from scratch with different parameters, not just replay this one.

    job_id and circuit layout are NOT logged — verify() runs pre-
    submission, before either exists yet, so there's nothing real to put
    there. An honest scope boundary, not an oversight: this is a record of
    the pre-submission simulation/hardware-aware pass, not a live-hardware
    job record.

    Silently no-ops if either check wasn't applicable — nothing to compare.
    """
    if not (old_check.get("applicable") and new_check.get("applicable")):
        return

    import datetime
    old_verdict = old_check["within_tolerance"]
    new_verdict = new_check["equivalent_at_alpha"]
    margin = new_check["equivalence_margin"]
    ci = new_check["confidence_interval"]
    conn = _connect()
    conn.execute(
        "INSERT INTO shadow_mode_comparisons (timestamp, provider, target_device, circuit_hash, "
        "total_shots, marked_shots, claimed_probability, equivalence_margin_lower, "
        "equivalence_margin_upper, alpha, ci_lower, ci_upper, ci_method, p_value, tost_verdict, "
        "old_check_within_tolerance, old_check_tolerance_used, agree) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (datetime.datetime.now(datetime.timezone.utc).isoformat(), provider, target_device,
         _circuit_hash(qasm_string), new_check["total_shots"], new_check["marked_shots"],
         new_check["claimed_probability"], margin["lower"], margin["upper"], new_check["alpha"],
         ci["lower"], ci["upper"], ci["method"], new_check["p_value"], new_check["tost_verdict"],
         int(old_verdict), old_check_tolerance, int(old_verdict == new_verdict)),
    )
    conn.commit()
    conn.close()


def shadow_mode_disagreement_log(limit: int = 50) -> dict:
    """
    Read back the shadow-mode comparison log. The whole point of shadow
    mode is reviewing this after real experiments accumulate — this
    doesn't recommend anything on its own, it just surfaces the raw
    disagreements (with full raw counts and statistical detail, not just a
    flag) so a human can look at them, re-derive the verdict under
    different parameters if needed, and decide whether the new equivalence
    test or the old tolerance band was right, case by case.
    """
    conn = _connect()
    total = conn.execute("SELECT COUNT(*) FROM shadow_mode_comparisons").fetchone()[0]
    disagreements = conn.execute(
        "SELECT timestamp, provider, target_device, circuit_hash, total_shots, marked_shots, "
        "claimed_probability, equivalence_margin_lower, equivalence_margin_upper, alpha, "
        "ci_lower, ci_upper, ci_method, p_value, tost_verdict, old_check_within_tolerance, "
        "old_check_tolerance_used FROM shadow_mode_comparisons WHERE agree = 0 "
        "ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()

    disagreement_rows = [
        {
            "timestamp": ts, "provider": prov, "target_device": dev, "circuit_hash": ch,
            "total_shots": shots, "marked_shots": marked, "claimed_probability": p0,
            "equivalence_margin": {"lower": margin_lo, "upper": margin_hi},
            "alpha": alpha, "confidence_interval": {"lower": ci_lo, "upper": ci_hi, "method": ci_m},
            "p_value": p, "new_check_tost_verdict": verdict,
            "old_check_said_within_tolerance": bool(old), "old_check_tolerance_used": old_tol,
        }
        for ts, prov, dev, ch, shots, marked, p0, margin_lo, margin_hi, alpha, ci_lo, ci_hi, ci_m,
            p, verdict, old, old_tol in disagreements
    ]

    return {
        "total_comparisons_logged": total,
        "disagreement_count": len(disagreement_rows),
        "disagreements": disagreement_rows,
        "note": (
            "No comparisons logged yet — both checks need to run together (via verify(), with "
            "expected_marked_bitstrings and expected_amplification supplied) before there's "
            "anything here." if total == 0 else
            f"{len(disagreement_rows)} of {total} logged comparisons disagreed (showing up to "
            f"{limit} most recent). Per the graduation criteria on aggregate_significance: read "
            "these after real experiments accumulate, not synthetic ones, before trusting either "
            "check to block on its own."
        ),
    }
