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
