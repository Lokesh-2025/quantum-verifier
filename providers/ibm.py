"""
IBM Quantum provider — device intelligence, job lifecycle, credit-aware
routing, and observability.

Copied from quantum-hardware-mcp/server.py (the original, untouched repo)
and adapted to be a plain, importable module with no MCP dependency — MCP
wrapping happens in mcp_server.py, one level up, not here.

Two real adaptations from the original, both because this project doesn't
copy snapshot.py (the 2-hour calibration pipeline) in this phase:
  1. Job-submission logging is now a small, self-contained local table
     instead of depending on an external `snapshot` module.
  2. The local calibration-history table only has the columns this file's
     own _save_snapshots() can actually populate. Extended fields that the
     original relied on a separate richer pipeline to backfill (T1/T2
     history, CLOPS, native gate set, etc.) are still queryable — they'll
     just read back as null here until/unless a fuller snapshot pipeline is
     added to this project later. That's an honest, working default, not a
     silent gap.
"""
import os
import json
import sqlite3
import math
from contextlib import contextmanager
import contextvars
from datetime import datetime, timedelta, timezone

from qiskit import QuantumCircuit
from qiskit import qasm3 as qiskit_qasm3
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from qiskit_ibm_runtime import SamplerV2 as Sampler
from qiskit_ibm_runtime import QiskitRuntimeService

from core.schema_guard import assert_schema_matches

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "ibm_history.db")


def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS device_snapshots (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                ts                TEXT    NOT NULL,
                name              TEXT    NOT NULL,
                num_qubits        INTEGER,
                operational       INTEGER,
                pending_jobs      INTEGER,
                avg_cx_error      REAL,
                avg_readout_error REAL,
                median_t1_us      REAL,
                median_t2_us      REAL,
                qubit_yield_fraction REAL,
                day_of_week       TEXT,
                hour_utc          INTEGER,
                processor_family  TEXT,
                backend_version   TEXT,
                last_calibration_dt TEXT,
                clops_h           REAL,
                quantum_volume    INTEGER,
                avg_2q_gate_duration_ns REAL,
                avg_prob_meas0_prep1 REAL,
                avg_prob_meas1_prep0 REAL,
                provider          TEXT,
                online_date       TEXT,
                dt_ns             REAL,
                avg_readout_length_ns REAL,
                rep_delay_default_ms REAL,
                native_gate_set   TEXT,
                coupling_map_edges TEXT,
                connectivity_density REAL,
                max_shots         INTEGER,
                max_experiments   INTEGER
            )
        """)
        assert_schema_matches(con, "device_snapshots", [
            "id", "ts", "name", "num_qubits", "operational", "pending_jobs",
            "avg_cx_error", "avg_readout_error", "median_t1_us", "median_t2_us",
            "qubit_yield_fraction", "day_of_week", "hour_utc", "processor_family",
            "backend_version", "last_calibration_dt", "clops_h", "quantum_volume",
            "avg_2q_gate_duration_ns", "avg_prob_meas0_prep1", "avg_prob_meas1_prep0",
            "provider", "online_date", "dt_ns", "avg_readout_length_ns",
            "rep_delay_default_ms", "native_gate_set", "coupling_map_edges",
            "connectivity_density", "max_shots", "max_experiments",
        ])
        con.execute("CREATE INDEX IF NOT EXISTS idx_name_ts ON device_snapshots (name, ts)")
        con.execute("""
            CREATE TABLE IF NOT EXISTS repro_experiments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_ts  TEXT NOT NULL,
                device_name TEXT NOT NULL,
                circuit     TEXT NOT NULL,
                n_runs      INTEGER NOT NULL,
                shots       INTEGER NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending'
            )
        """)
        assert_schema_matches(con, "repro_experiments", [
            "id", "created_ts", "device_name", "circuit", "n_runs", "shots", "status",
        ])
        con.execute("""
            CREATE TABLE IF NOT EXISTS repro_runs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id INTEGER NOT NULL REFERENCES repro_experiments(id),
                run_index     INTEGER NOT NULL,
                submitted_ts  TEXT NOT NULL,
                job_id        TEXT,
                status        TEXT NOT NULL DEFAULT 'submitted',
                counts        TEXT,
                calibration_epoch TEXT
            )
        """)
        assert_schema_matches(con, "repro_runs", [
            "id", "experiment_id", "run_index", "submitted_ts", "job_id",
            "status", "counts", "calibration_epoch",
        ])
        con.execute("""
            CREATE TABLE IF NOT EXISTS job_submissions (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id                    TEXT,
                tool_name                 TEXT,
                backend_name              TEXT,
                circuit_qubits            INTEGER,
                circuit_depth_raw         INTEGER,
                circuit_depth_transpiled  INTEGER,
                shots_requested           INTEGER,
                ts                        TEXT
            )
        """)
        assert_schema_matches(con, "job_submissions", [
            "id", "job_id", "tool_name", "backend_name", "circuit_qubits",
            "circuit_depth_raw", "circuit_depth_transpiled", "shots_requested", "ts",
        ])

        # Per-qubit/per-pair calibration archive, ported from
        # quantum-hardware-mcp 2026-08-24 — see check_chip_identity below.
        con.executescript("""
            CREATE TABLE IF NOT EXISTS qubit_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_name TEXT NOT NULL, qubit_index INTEGER NOT NULL,
                property_name TEXT NOT NULL, value REAL, unit TEXT,
                vendor_measured_at TEXT NOT NULL, polled_at TEXT NOT NULL,
                UNIQUE(device_name, qubit_index, property_name, vendor_measured_at)
            );
            CREATE INDEX IF NOT EXISTS idx_qubit_snapshots_lookup
                ON qubit_snapshots (device_name, qubit_index, property_name, vendor_measured_at);

            CREATE TABLE IF NOT EXISTS pair_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_name TEXT NOT NULL, qubit1 INTEGER NOT NULL, qubit2 INTEGER NOT NULL,
                gate_name TEXT NOT NULL, property_name TEXT NOT NULL, value REAL, unit TEXT,
                vendor_measured_at TEXT NOT NULL, polled_at TEXT NOT NULL,
                UNIQUE(device_name, qubit1, qubit2, gate_name, property_name, vendor_measured_at)
            );
            CREATE INDEX IF NOT EXISTS idx_pair_snapshots_lookup
                ON pair_snapshots (device_name, qubit1, qubit2, vendor_measured_at);

            CREATE TABLE IF NOT EXISTS raw_properties_archive (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_name TEXT NOT NULL, vendor_measured_at TEXT NOT NULL,
                raw_json_gzip BLOB NOT NULL, polled_at TEXT NOT NULL,
                UNIQUE(device_name, vendor_measured_at)
            );
        """)
        assert_schema_matches(con, "qubit_snapshots", [
            "id", "device_name", "qubit_index", "property_name", "value", "unit",
            "vendor_measured_at", "polled_at",
        ])
        assert_schema_matches(con, "pair_snapshots", [
            "id", "device_name", "qubit1", "qubit2", "gate_name", "property_name",
            "value", "unit", "vendor_measured_at", "polled_at",
        ])
        assert_schema_matches(con, "raw_properties_archive", [
            "id", "device_name", "vendor_measured_at", "raw_json_gzip", "polled_at",
        ])


def _save_snapshots(rows: list) -> None:
    """
    Real bug found and fixed 2026-08-24: this only ever wrote 7 of the
    table's ~25 columns, and get_device_details() computes T1/T2 under
    different key names (avg_t1_us/avg_t2_us) than what this function and
    get_alerts() actually use (median_t1_us/median_t2_us) — a silent
    mismatch. Result: all 72 rows saved before this fix have every
    calibration field null, so get_alerts()'s drift detection has never
    been able to fire on real data, the same class of bug as the IonQ
    null-calibration incident in the sibling quantum-hardware-mcp repo.
    Now accepts either key name for T1/T2 and saves what's available.
    """
    ts = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as con:
        con.executemany(
            """
            INSERT INTO device_snapshots
                (ts, name, num_qubits, operational, pending_jobs,
                 avg_cx_error, avg_readout_error, median_t1_us, median_t2_us)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (ts, r["name"], r.get("num_qubits"),
                 int(r["operational"]) if r.get("operational") is not None else None,
                 r.get("pending_jobs"), r.get("avg_cx_error"), r.get("avg_readout_error"),
                 r.get("median_t1_us", r.get("avg_t1_us")),
                 r.get("median_t2_us", r.get("avg_t2_us")))
                for r in rows
            ],
        )


def _recent_drift_alert(device_name: str, hours: int = 24) -> dict | None:
    """
    Real, fresh calibration problem for this exact device, or None. Ported
    2026-08-24 from quantum-hardware-mcp's submit_job gate (same fix,
    adapted to this project's own smaller local db rather than sharing
    files across the two repos, which should stay independent). Checks
    T1/T2 drops AND cx/readout-error spikes — get_alerts() here previously
    only checked T1/T2.
    """
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute(
            """
            WITH ranked AS (
                SELECT ts, median_t1_us, median_t2_us, avg_cx_error, avg_readout_error,
                    LAG(median_t1_us) OVER (ORDER BY ts) AS prev_t1,
                    LAG(median_t2_us) OVER (ORDER BY ts) AS prev_t2,
                    LAG(avg_cx_error) OVER (ORDER BY ts) AS prev_cx,
                    LAG(avg_readout_error) OVER (ORDER BY ts) AS prev_ro
                FROM device_snapshots
                WHERE name = ? AND ts >= datetime('now', ? || ' hours')
            )
            SELECT ts, median_t1_us, prev_t1, median_t2_us, prev_t2,
                   avg_cx_error, prev_cx, avg_readout_error, prev_ro
            FROM ranked
            WHERE (prev_t1 IS NOT NULL AND prev_t1 > 0 AND (prev_t1 - median_t1_us) / prev_t1 > 0.20)
               OR (prev_t2 IS NOT NULL AND prev_t2 > 0 AND (prev_t2 - median_t2_us) / prev_t2 > 0.20)
               OR (prev_cx IS NOT NULL AND prev_cx > 0 AND (avg_cx_error - prev_cx) / prev_cx > 0.20)
               OR (prev_ro IS NOT NULL AND prev_ro > 0 AND (avg_readout_error - prev_ro) / prev_ro > 0.20)
            ORDER BY ts DESC LIMIT 1
            """,
            (device_name, f"-{hours}"),
        ).fetchone()
        if not row:
            return None
        ts, t1, prev_t1, t2, prev_t2, cx, prev_cx, ro, prev_ro = row
        if prev_t1 and prev_t1 > 0 and (prev_t1 - t1) / prev_t1 > 0.20:
            return {"ts": ts, "type": "t1_drop", "prev_value": prev_t1, "curr_value": t1}
        if prev_t2 and prev_t2 > 0 and (prev_t2 - t2) / prev_t2 > 0.20:
            return {"ts": ts, "type": "t2_drop", "prev_value": prev_t2, "curr_value": t2}
        if prev_cx and prev_cx > 0 and (cx - prev_cx) / prev_cx > 0.20:
            return {"ts": ts, "type": "cx_error_spike", "prev_value": prev_cx, "curr_value": cx}
        if prev_ro and prev_ro > 0 and (ro - prev_ro) / prev_ro > 0.20:
            return {"ts": ts, "type": "readout_error_spike", "prev_value": prev_ro, "curr_value": ro}
    return None


def _save_qubit_and_pair_snapshot(device_name: str, props) -> None:
    """
    Ported from quantum-hardware-mcp 2026-08-24. Writes one live
    properties() snapshot into the per-qubit/per-pair archive, using the
    same properties() call get_device_details() already makes — no extra
    API request. Dedupes for free via the UNIQUE constraints, so calling
    this every collection cycle is cheap even though individual properties
    only change roughly daily.
    """
    import gzip

    polled_at = datetime.now(timezone.utc).isoformat()
    raw = {
        "last_update_date": str(props.last_update_date),
        "qubits": [
            [{"name": nduv.name, "value": nduv.value, "unit": nduv.unit, "date": str(nduv.date)}
             for nduv in q]
            for q in props.qubits
        ],
        "gates": [
            {"gate": g.gate, "qubits": list(g.qubits),
             "parameters": [{"name": p.name, "value": p.value, "unit": p.unit, "date": str(p.date)}
                            for p in g.parameters]}
            for g in props.gates
        ],
    }
    blob = gzip.compress(json.dumps(raw).encode("utf-8"))

    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO raw_properties_archive "
            "(device_name, vendor_measured_at, raw_json_gzip, polled_at) VALUES (?, ?, ?, ?)",
            (device_name, str(props.last_update_date), blob, polled_at),
        )
        for qi, qubit_params in enumerate(props.qubits):
            for nduv in qubit_params:
                cur.execute(
                    "INSERT OR IGNORE INTO qubit_snapshots "
                    "(device_name, qubit_index, property_name, value, unit, vendor_measured_at, polled_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (device_name, qi, nduv.name, nduv.value, nduv.unit, str(nduv.date), polled_at),
                )
        for g in props.gates:
            if len(g.qubits) != 2:
                continue
            q1, q2 = g.qubits
            for p in g.parameters:
                cur.execute(
                    "INSERT OR IGNORE INTO pair_snapshots "
                    "(device_name, qubit1, qubit2, gate_name, property_name, value, unit, "
                    " vendor_measured_at, polled_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (device_name, q1, q2, g.gate, p.name, p.value, p.unit, str(p.date), polled_at),
                )
        con.commit()


def _log_job_submission(job_id, tool_name, backend_name, circuit_qubits,
                         circuit_depth_raw, circuit_depth_transpiled, shots_requested) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            INSERT INTO job_submissions
                (job_id, tool_name, backend_name, circuit_qubits,
                 circuit_depth_raw, circuit_depth_transpiled, shots_requested, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (job_id, tool_name, backend_name, circuit_qubits, circuit_depth_raw,
              circuit_depth_transpiled, shots_requested,
              datetime.now(timezone.utc).isoformat()))


_init_db()

_token_override = contextvars.ContextVar("token_override", default=None)


@contextmanager
def use_ibm_token(token):
    if not token:
        yield
        return
    reset = _token_override.set(token)
    try:
        yield
    finally:
        _token_override.reset(reset)


def _get_service() -> QiskitRuntimeService:
    token = _token_override.get() or os.getenv("IBM_QUANTUM_TOKEN")
    if not token:
        raise ValueError(
            "IBM_QUANTUM_TOKEN is not set. Create a .env file with:\n"
            "  IBM_QUANTUM_TOKEN=your_token_here\n"
            "Get your token at https://quantum.ibm.com/account"
        )
    channel = os.getenv("IBM_CHANNEL", "ibm_quantum_platform")
    instance = os.getenv("IBM_INSTANCE")
    kwargs = dict(channel=channel, token=token)
    if instance:
        kwargs["instance"] = instance
    return QiskitRuntimeService(**kwargs)


def _cx_errors_for_backend(props) -> list:
    if props is None:
        return []
    TWO_QUBIT_GATES = {"cx", "ecr", "cz"}
    errors = []
    for gate in props.gates:
        if gate.gate in TWO_QUBIT_GATES and gate.parameters:
            errors.append(gate.parameters[0].value)
    return errors


def _safe_t(fn, q):
    try:
        return fn(q)
    except Exception:
        return None


def list_devices() -> list:
    """All accessible IBM backends with live operational status."""
    service = _get_service()
    backends = service.backends()
    devices = []
    for backend in backends:
        status = backend.status()
        devices.append({
            "name": backend.name,
            "num_qubits": backend.num_qubits,
            "status": status.status_msg,
            "operational": status.operational,
            "pending_jobs": status.pending_jobs,
        })
    devices.sort(key=lambda d: d["num_qubits"], reverse=True)
    _save_snapshots(devices)
    return devices


def get_device_details(device_name: str) -> dict:
    """Per-qubit T1/T2, readout error, gate error for one device."""
    service = _get_service()
    backend = service.backend(device_name)
    status = backend.status()
    result = {
        "name": backend.name, "num_qubits": backend.num_qubits,
        "status": status.status_msg, "operational": status.operational,
        "pending_jobs": status.pending_jobs,
    }
    props = backend.properties()
    if props:
        try:
            _save_qubit_and_pair_snapshot(backend.name, props)
        except Exception as e:
            print(f"per-qubit archive save failed for {backend.name}: {e}")
        readout_errors = [props.readout_error(q) for q in range(backend.num_qubits)
                           if props.readout_error(q) is not None]
        if readout_errors:
            result["avg_readout_error"] = round(sum(readout_errors) / len(readout_errors), 5)
        cx_errors = _cx_errors_for_backend(props)
        if cx_errors:
            result["avg_cx_error"] = round(sum(cx_errors) / len(cx_errors), 5)
            result["best_cx_error"] = round(min(cx_errors), 5)
            result["worst_cx_error"] = round(max(cx_errors), 5)
        t1_times = [v for q in range(backend.num_qubits) if (v := _safe_t(props.t1, q)) is not None]
        t2_times = [v for q in range(backend.num_qubits) if (v := _safe_t(props.t2, q)) is not None]
        if t1_times:
            result["avg_t1_us"] = round(sum(t1_times) / len(t1_times) * 1e6, 1)
        if t2_times:
            result["avg_t2_us"] = round(sum(t2_times) / len(t2_times) * 1e6, 1)
        result["last_calibration"] = str(props.last_update_date)
    else:
        result["note"] = "No calibration data available (simulator or uncalibrated device)"
    _save_snapshots([result])
    return result


def best_qubits(device_name: str, n: int = 5) -> dict:
    """Best n individual qubits on a device by live calibration."""
    service = _get_service()
    backend = service.backend(device_name)
    props = backend.properties()
    if not props:
        return {"error": f"{device_name} has no calibration data available."}
    n = min(n, backend.num_qubits)
    TWO_QUBIT_GATES = {"cx", "ecr", "cz"}
    qubit_best_cx = {}
    for gate in props.gates:
        if gate.gate in TWO_QUBIT_GATES and gate.parameters:
            err = gate.parameters[0].value
            for q in gate.qubits:
                if q not in qubit_best_cx or err < qubit_best_cx[q]:
                    qubit_best_cx[q] = err
    qubit_data = []
    for q in range(backend.num_qubits):
        ro = props.readout_error(q)
        cx = qubit_best_cx.get(q)
        t1 = _safe_t(props.t1, q)
        t2 = _safe_t(props.t2, q)
        score = (ro if ro is not None else 1.0) + (cx if cx is not None else 1.0)
        qubit_data.append({
            "qubit": q, "score": round(score, 6),
            "readout_error": round(ro, 5) if ro is not None else None,
            "best_cx_error": round(cx, 5) if cx is not None else None,
            "t1_us": round(t1 * 1e6, 1) if t1 is not None else None,
            "t2_us": round(t2 * 1e6, 1) if t2 is not None else None,
        })
    qubit_data.sort(key=lambda q: q["score"])
    top_n = qubit_data[:n]
    top_indices = {q["qubit"] for q in top_n}
    coupling_map = backend.coupling_map
    connected_pairs = []
    disconnected_warning = None
    if coupling_map is not None:
        edges = list(coupling_map.get_edges())
        connected_pairs = [[a, b] for a, b in edges if a in top_indices and b in top_indices]
        if len(connected_pairs) < n - 1:
            disconnected_warning = (
                f"WARNING: the top {n} qubits by score are NOT all connected on "
                f"{device_name}'s coupling map. Only {len(connected_pairs)} direct links found."
            )
    return {
        "device": device_name, "n": n,
        "scoring": "readout_error + best_cx_error (lower = better)",
        "best_qubits": top_n,
        "connectivity": {"direct_links_between_top_qubits": connected_pairs,
                          "warning": disconnected_warning},
    }


def best_qubits_for_reproducibility(device_name: str, n: int = 5, min_history: int = 3) -> dict:
    """
    best_qubits() picks by current error alone — fine for a one-off
    submission, but for start_repro_experiment/repro_score, a qubit that's
    momentarily good but volatile is a worse choice than one that's
    reliably decent, since you're comparing the same circuit across
    multiple real runs over time. Added 2026-08-24, uses the real per-qubit
    T1 history now being collected (qubit_snapshots) — needs real history
    to be meaningful, so honestly reports low_confidence for qubits with
    too few real readings rather than pretending they're proven stable.

    Score = current readout_error + best_cx_error (same as best_qubits)
    PLUS a volatility penalty: the coefficient of variation (std/mean) of
    that qubit's own real T1 history — a qubit whose T1 swings around a
    lot is a worse bet for a multi-run comparison than one that doesn't,
    even if its current T1 happens to look fine right now.
    """
    import statistics as _stats

    base = best_qubits(device_name, n=10_000)  # best_qubits clamps to the real qubit count itself
    if "error" in base:
        return base

    with sqlite3.connect(DB_PATH) as con:
        history = {}
        rows = con.execute(
            "SELECT qubit_index, value FROM qubit_snapshots "
            "WHERE device_name = ? AND property_name = 'T1' ORDER BY qubit_index, vendor_measured_at",
            (device_name,),
        ).fetchall()
        for qi, val in rows:
            history.setdefault(qi, []).append(val)

    scored = []
    for q in base["best_qubits"]:
        qi = q["qubit"]
        t1_history = history.get(qi, [])
        if len(t1_history) >= min_history and _stats.mean(t1_history) > 0:
            cv = _stats.stdev(t1_history) / _stats.mean(t1_history)
            volatility_penalty = cv
            low_confidence = False
        else:
            cv = None
            volatility_penalty = 0.0  # no history yet -- don't penalize, but flag it
            low_confidence = True
        scored.append({
            **q,
            "t1_history_n": len(t1_history),
            "t1_coefficient_of_variation": round(cv, 4) if cv is not None else None,
            "stability_adjusted_score": round(q["score"] + volatility_penalty, 6),
            "low_confidence": low_confidence,
        })

    scored.sort(key=lambda q: q["stability_adjusted_score"])
    top_n = scored[:n]

    return {
        "device": device_name, "n": n,
        "scoring": "readout_error + best_cx_error + T1 coefficient-of-variation "
                   "(lower = better AND more stable)",
        "best_qubits_for_reproducibility": top_n,
        "note": "low_confidence=true means fewer than "
                f"{min_history} real historical T1 readings exist for that qubit yet — "
                "its stability is genuinely unproven, not assumed good, so treat that "
                "ranking with real caution until more history accumulates.",
    }


def compare_devices(sort_by: str = "cx_error") -> dict:
    """Rank all accessible IBM devices by cx_error, queue, qubits, or combined score."""
    service = _get_service()
    backends = service.backends()
    devices = []
    for backend in backends:
        status = backend.status()
        entry = {
            "name": backend.name, "num_qubits": backend.num_qubits,
            "pending_jobs": status.pending_jobs, "operational": status.operational,
            "status": "online" if status.operational else "offline",
        }
        try:
            props = backend.properties()
            cx_errors = _cx_errors_for_backend(props)
            if cx_errors:
                entry["avg_cx_error"] = round(sum(cx_errors) / len(cx_errors), 5)
            readout_errors = [props.readout_error(q) for q in range(backend.num_qubits)
                               if props.readout_error(q) is not None]
            if readout_errors:
                entry["avg_readout_error"] = round(sum(readout_errors) / len(readout_errors), 5)
            t1_times = [v for q in range(backend.num_qubits) if (v := _safe_t(props.t1, q)) is not None]
            t2_times = [v for q in range(backend.num_qubits) if (v := _safe_t(props.t2, q)) is not None]
            if t1_times:
                entry["avg_t1_us"] = round(sum(t1_times) / len(t1_times), 1)
            if t2_times:
                entry["avg_t2_us"] = round(sum(t2_times) / len(t2_times), 1)
        except Exception:
            pass
        devices.append(entry)

    if sort_by == "cx_error":
        devices.sort(key=lambda d: d.get("avg_cx_error", float("inf")))
    elif sort_by == "queue":
        devices.sort(key=lambda d: d.get("pending_jobs", float("inf")))
    elif sort_by == "qubits":
        devices.sort(key=lambda d: d["num_qubits"], reverse=True)
    elif sort_by == "combined":
        cx_vals = [d["avg_cx_error"] for d in devices if d.get("avg_cx_error") is not None]
        q_vals = [d["pending_jobs"] for d in devices if d.get("pending_jobs") is not None]
        min_cx, max_cx = (min(cx_vals), max(cx_vals)) if cx_vals else (0, 1)
        min_q, max_q = (min(q_vals), max(q_vals)) if q_vals else (0, 1)
        cx_range = max_cx - min_cx or 1
        q_range = max_q - min_q or 1
        for d in devices:
            cx = d.get("avg_cx_error")
            q = d.get("pending_jobs")
            norm_cx = (cx - min_cx) / cx_range if cx is not None else 1.0
            norm_q = (q - min_q) / q_range if q is not None else 1.0
            d["combined_score"] = round(0.7 * norm_cx + 0.3 * norm_q, 4)
        devices.sort(key=lambda d: d.get("combined_score", float("inf")))
    else:
        return {"error": f"Unknown sort_by '{sort_by}'. Use cx_error, queue, qubits, or combined."}

    for i, device in enumerate(devices):
        device["rank"] = i + 1
    _save_snapshots(devices)
    return {"sorted_by": sort_by, "devices": devices}


def queue_status() -> list:
    """Current queue snapshot across all backends."""
    service = _get_service()
    backends = service.backends()
    queues = []
    for backend in backends:
        status = backend.status()
        queues.append({
            "name": backend.name, "num_qubits": backend.num_qubits,
            "pending_jobs": status.pending_jobs, "status": status.status_msg,
            "operational": status.operational,
        })
    queues.sort(key=lambda d: d["pending_jobs"])
    _save_snapshots(queues)
    return queues


def device_history(device_name: str, days: int = 7) -> dict:
    """Calibration snapshots over the last N days from this project's own local history."""
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT ts, num_qubits, operational, pending_jobs, avg_cx_error, avg_readout_error,
                   median_t1_us, median_t2_us, qubit_yield_fraction, day_of_week, hour_utc,
                   processor_family, backend_version, last_calibration_dt, clops_h,
                   quantum_volume, avg_2q_gate_duration_ns, avg_prob_meas0_prep1, avg_prob_meas1_prep0
            FROM device_snapshots WHERE name = ? AND ts >= datetime('now', ? || ' days')
            ORDER BY ts ASC
        """, (device_name, f"-{days}")).fetchall()
    return {"device": device_name, "days": days, "snapshots": [dict(r) for r in rows]}


def device_profile(device_name: str) -> dict:
    """Complete hardware profile from the most recent local snapshot."""
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("""
            SELECT * FROM device_snapshots WHERE name = ? ORDER BY ts DESC LIMIT 1
        """, (device_name,)).fetchone()
    if row is None:
        return {"error": f"No snapshot found for '{device_name}'. Call list_devices first."}
    return dict(row)


def device_on_date(device_name: str, date: str) -> dict:
    """Historical stats for a device on a specific past date."""
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT ts, operational, pending_jobs, avg_cx_error, avg_readout_error
            FROM device_snapshots WHERE name = ? AND date(ts) = ? ORDER BY ts ASC
        """, (device_name, date)).fetchall()
    if not rows:
        return {"device": device_name, "date": date, "found": False,
                "note": "No snapshots found for this device on this date."}
    snapshots = [dict(r) for r in rows]

    def _avg(field):
        vals = [s[field] for s in snapshots if s[field] is not None]
        return round(sum(vals) / len(vals), 5) if vals else None

    return {
        "device": device_name, "date": date, "found": True,
        "snapshots_that_day": len(snapshots),
        "first_snapshot": snapshots[0]["ts"], "last_snapshot": snapshots[-1]["ts"],
        "avg_pending_jobs": _avg("pending_jobs"), "avg_cx_error": _avg("avg_cx_error"),
        "avg_readout_error": _avg("avg_readout_error"),
    }


def submit_job(device_name: str, qasm_string: str, shots: int = 1024, qasm_version: int = 2,
                initial_layout: list = None, confirm_despite_drift_alert: bool = False) -> dict:
    """
    Compile and submit a circuit to an IBM quantum computer.

    initial_layout : optional list of physical qubit indices, one per
        logical qubit in order. Without it, the transpiler picks its own
        layout automatically -- fine in general, but it means a separately
        verified qubit selection (e.g. specific low-error qubits, confirmed
        SWAP-free for a specific circuit) is NOT guaranteed to be what
        actually runs. Pass it explicitly whenever the submission needs to
        match a layout that was already checked.
    confirm_despite_drift_alert : must be True to submit anyway if this
        device had a real calibration alert (T1/T2 drop, or cx/readout
        error spike) in the last 24 hours, checked automatically against
        this project's own local history. Ported from quantum-hardware-mcp
        2026-08-24.
    """
    try:
        circuit = qiskit_qasm3.loads(qasm_string) if qasm_version == 3 else QuantumCircuit.from_qasm_str(qasm_string)
    except Exception as e:
        return {"error": f"Failed to parse QASM {qasm_version}: {e}"}
    shots = max(1, min(shots, 20000))

    drift_alert = _recent_drift_alert(device_name)
    if drift_alert and not confirm_despite_drift_alert:
        return {
            "error": f"'{device_name}' had a recent calibration alert: {drift_alert['type']} "
                     f"at {drift_alert['ts']}.",
            "hint": "Pass confirm_despite_drift_alert=True to submit anyway. "
                    "Call get_alerts to see the full recent trend first.",
            "drift_alert": drift_alert,
        }

    service = _get_service()
    try:
        backend = service.backend(device_name)
    except Exception as e:
        return {"error": f"Device '{device_name}' not found: {e}"}
    quota_check = _check_ibm_quota_before_submitting(device_name, circuit, shots)
    if quota_check.get("error"):
        return quota_check
    if initial_layout is not None and len(initial_layout) != circuit.num_qubits:
        return {"error": f"initial_layout has {len(initial_layout)} entries but the circuit has "
                          f"{circuit.num_qubits} qubits — must match exactly."}
    pm = generate_preset_pass_manager(backend=backend, optimization_level=1, initial_layout=initial_layout)
    isa_circuit = pm.run(circuit)
    sampler = Sampler(mode=backend)
    job = sampler.run([isa_circuit], shots=shots)
    _log_job_submission(job.job_id(), "submit_job", device_name, circuit.num_qubits,
                         circuit.depth(), isa_circuit.depth(), shots)
    return {
        "job_id": job.job_id(), "status": str(job.status()),
        "device": device_name, "shots": shots,
        "initial_layout_used": initial_layout,
    }


def job_status(job_id: str) -> dict:
    """Status of a submitted job."""
    service = _get_service()
    try:
        job = service.job(job_id)
    except Exception as e:
        return {"error": f"Job '{job_id}' not found: {e}"}
    status = str(job.status())
    result = {"job_id": job_id, "status": status, "backend": job.backend().name}
    try:
        result["creation_date"] = str(job.creation_date)
    except Exception:
        pass
    if status == "QUEUED":
        try:
            pos = job.queue_position()
            if pos is not None:
                result["queue_position"] = pos
        except Exception:
            pass
    elif status == "ERROR":
        try:
            result["error_message"] = job.error_message()
        except Exception:
            pass
    return result


def job_results(job_id: str) -> dict:
    """Measurement counts from a completed job."""
    service = _get_service()
    try:
        job = service.job(job_id)
    except Exception as e:
        return {"error": f"Job '{job_id}' not found: {e}"}
    status = str(job.status())
    if status != "DONE":
        return {"job_id": job_id, "status": status, "note": "Job not complete yet."}
    try:
        result = job.result()
    except Exception as e:
        return {"error": f"Failed to retrieve results: {e}"}
    try:
        pub_result = result[0]
        if hasattr(pub_result.data, "evs"):
            evs = pub_result.data.evs
            stds = getattr(pub_result.data, "stds", None)
            return {
                "job_id": job_id, "status": "DONE", "backend": job.backend().name,
                "type": "estimator",
                "expectation_value": float(evs) if hasattr(evs, "__float__") else list(evs),
                "std_error": float(stds) if stds is not None and hasattr(stds, "__float__") else None,
            }
        counts_by_register = {}
        for reg_name, bit_array in vars(pub_result.data).items():
            counts_by_register[reg_name] = bit_array.get_counts()
    except Exception as e:
        return {"error": f"Failed to parse result data: {e}"}
    counts = list(counts_by_register.values())[0] if len(counts_by_register) == 1 else counts_by_register
    total_shots = sum(counts.values()) if isinstance(counts, dict) else None
    return {
        "job_id": job_id, "status": "DONE", "backend": job.backend().name,
        "total_shots": total_shots, "counts": counts,
    }


def cancel_job(job_id: str) -> dict:
    """Cancel a queued or running job."""
    service = _get_service()
    try:
        job = service.job(job_id)
    except Exception as e:
        return {"error": f"Job '{job_id}' not found: {e}"}
    status = str(job.status())
    if status in ("DONE", "ERROR", "CANCELLED"):
        return {"job_id": job_id, "error": f"Cannot cancel a job with status '{status}'.",
                "current_status": status}
    try:
        job.cancel()
    except Exception as e:
        return {"error": f"Cancel request failed: {e}"}
    return {"job_id": job_id, "status": "CANCELLED"}


def list_jobs(limit: int = 10) -> dict:
    """Most recently submitted jobs."""
    limit = max(1, min(limit, 50))
    service = _get_service()
    try:
        jobs = service.jobs(limit=limit)
    except Exception as e:
        return {"error": f"Failed to fetch jobs: {e}"}
    results = []
    for job in jobs:
        entry = {"job_id": job.job_id(), "status": str(job.status())}
        try:
            entry["backend"] = job.backend().name
        except Exception:
            entry["backend"] = None
        try:
            entry["created"] = str(job.creation_date)
        except Exception:
            entry["created"] = None
        results.append(entry)
    return {"count": len(results), "jobs": results}


def _estimate_minutes(backend, qc, shots: int) -> dict:
    status = backend.status()
    pending = status.pending_jobs or 0
    try:
        pm = generate_preset_pass_manager(backend=backend, optimization_level=1)
        isa = pm.run(qc)
        depth = isa.depth()
        n_qubits = isa.num_qubits
    except Exception:
        depth = qc.depth()
        n_qubits = qc.num_qubits
    queue_secs = pending * 30
    exec_secs = (shots * depth * 1e-6) + 2
    total_secs = queue_secs + exec_secs
    return {
        "pending_jobs_in_queue": pending, "circuit_depth_after_transpile": depth,
        "num_qubits": n_qubits, "queue_wait_estimate_mins": round(queue_secs / 60, 2),
        "execution_estimate_mins": round(exec_secs / 60, 4),
        "total_estimate_mins": round(total_secs / 60, 2),
    }


def estimate_runtime(circuit: str, backend_name: str, shots: int = 1024) -> dict:
    """Estimate QPU minutes for a circuit before submitting."""
    try:
        try:
            qc = QuantumCircuit.from_qasm_str(circuit)
        except Exception:
            qc = qiskit_qasm3.loads(circuit)
        service = _get_service()
        backend = service.backend(backend_name)
        est = _estimate_minutes(backend, qc, shots)
        est["device"] = backend_name
        est["shots"] = shots
        if est["total_estimate_mins"] > 10:
            est["warning"] = f"This job may cost ~{est['total_estimate_mins']} min."
        return est
    except Exception as e:
        return {"error": str(e)}


def route_job(circuit: str, shots: int = 1024, max_minutes: float = 10.0) -> dict:
    """Recommend the best IBM device for a circuit based on cost and quality."""
    try:
        try:
            qc = QuantumCircuit.from_qasm_str(circuit)
        except Exception:
            qc = qiskit_qasm3.loads(circuit)
        required_qubits = qc.num_qubits
        service = _get_service()
        backends = service.backends(operational=True)
        rankings, skipped = [], []
        for backend in backends:
            if backend.num_qubits < required_qubits:
                skipped.append({"device": backend.name,
                                 "reason": f"only {backend.num_qubits} qubits, needs {required_qubits}"})
                continue
            try:
                est = _estimate_minutes(backend, qc, shots)
                total = est["total_estimate_mins"]
                if total > max_minutes:
                    skipped.append({"device": backend.name,
                                     "reason": f"estimated {total} min exceeds {max_minutes} min budget"})
                    continue
                props = backend.properties()
                cx_errors = _cx_errors_for_backend(props) if props else []
                avg_cx = round(sum(cx_errors) / len(cx_errors), 5) if cx_errors else None
                rankings.append({
                    "device": backend.name, "num_qubits": backend.num_qubits,
                    "estimated_mins": total, "queue_wait_mins": est["queue_wait_estimate_mins"],
                    "circuit_depth": est["circuit_depth_after_transpile"], "avg_cx_error": avg_cx,
                })
            except Exception as e:
                skipped.append({"device": backend.name, "reason": str(e)})
        rankings.sort(key=lambda x: (x["estimated_mins"], x["avg_cx_error"] or 1))
        if not rankings:
            return {"error": "No devices fit within your budget or qubit requirements.", "skipped": skipped}
        recommendation = rankings[0]
        return {
            "recommendation": recommendation["device"],
            "reason": f"Lowest estimated cost ({recommendation['estimated_mins']} min) "
                      f"with avg_cx_error {recommendation['avg_cx_error']}",
            "ranked_devices": rankings, "skipped_devices": skipped,
            "circuit_requires_qubits": required_qubits, "budget_max_minutes": max_minutes,
        }
    except Exception as e:
        return {"error": str(e)}


def get_alerts(device_name: str = "", days: int = 7) -> dict:
    """
    Calibration drift alerts from this project's own local history.

    Previously only checked T1/T2 drops — cx-error and readout-error
    spikes were silently not covered at all. Fixed 2026-08-24 alongside
    the _save_snapshots key-name-mismatch bug that meant every alert here
    was computed against all-null data until now (see _save_snapshots'
    docstring for the full story).
    """
    if not os.path.exists(DB_PATH):
        return {"error": "No local database found yet — call list_devices first."}
    try:
        with sqlite3.connect(DB_PATH) as con:
            params = [f"-{max(1, int(days))}"]
            name_filter = ""
            if device_name:
                name_filter = "AND name = ?"
                params.append(device_name)
            rows = con.execute(f"""
                WITH ranked AS (
                    SELECT name, ts, median_t1_us, median_t2_us, avg_cx_error, avg_readout_error,
                        LAG(median_t1_us) OVER (PARTITION BY name ORDER BY ts) AS prev_t1,
                        LAG(median_t2_us) OVER (PARTITION BY name ORDER BY ts) AS prev_t2,
                        LAG(avg_cx_error) OVER (PARTITION BY name ORDER BY ts) AS prev_cx,
                        LAG(avg_readout_error) OVER (PARTITION BY name ORDER BY ts) AS prev_ro
                    FROM device_snapshots WHERE ts >= datetime('now', ? || ' days') {name_filter}
                )
                SELECT name, ts, median_t1_us, prev_t1, median_t2_us, prev_t2,
                       avg_cx_error, prev_cx, avg_readout_error, prev_ro
                FROM ranked
                WHERE (prev_t1 IS NOT NULL AND prev_t1 > 0 AND (prev_t1 - median_t1_us) / prev_t1 > 0.20)
                   OR (prev_t2 IS NOT NULL AND prev_t2 > 0 AND (prev_t2 - median_t2_us) / prev_t2 > 0.20)
                   OR (prev_cx IS NOT NULL AND prev_cx > 0 AND (avg_cx_error - prev_cx) / prev_cx > 0.20)
                   OR (prev_ro IS NOT NULL AND prev_ro > 0 AND (avg_readout_error - prev_ro) / prev_ro > 0.20)
                ORDER BY ts DESC LIMIT 100
            """, params).fetchall()
        alerts = []
        for name, ts, t1, prev_t1, t2, prev_t2, cx, prev_cx, ro, prev_ro in rows:
            if prev_t1 and prev_t1 > 0 and (prev_t1 - t1) / prev_t1 > 0.20:
                pct = round((prev_t1 - t1) / prev_t1 * 100, 1)
                alerts.append({"ts": ts, "device": name, "type": "t1_drop",
                                "message": f"{name} T1 dropped {pct}%"})
            if prev_t2 and prev_t2 > 0 and (prev_t2 - t2) / prev_t2 > 0.20:
                pct = round((prev_t2 - t2) / prev_t2 * 100, 1)
                alerts.append({"ts": ts, "device": name, "type": "t2_drop",
                                "message": f"{name} T2 dropped {pct}%"})
            if prev_cx and prev_cx > 0 and (cx - prev_cx) / prev_cx > 0.20:
                pct = round((cx - prev_cx) / prev_cx * 100, 1)
                alerts.append({"ts": ts, "device": name, "type": "cx_error_spike",
                                "message": f"{name} cx_error spiked {pct}%"})
            if prev_ro and prev_ro > 0 and (ro - prev_ro) / prev_ro > 0.20:
                pct = round((ro - prev_ro) / prev_ro * 100, 1)
                alerts.append({"ts": ts, "device": name, "type": "readout_error_spike",
                                "message": f"{name} readout_error spiked {pct}%"})
        alerts.sort(key=lambda a: a["ts"], reverse=True)
        if not alerts:
            return {"alerts": [], "message": f"No alerts in the last {days} day(s)"}
        return {"alerts": alerts, "total": len(alerts), "period_days": days}
    except Exception as e:
        return {"error": str(e)}


def _qubit_fingerprint_vector(device_name: str, as_of: str = None) -> dict:
    """Real per-qubit fingerprint from qubit_snapshots/pair_snapshots, ported
    from quantum-hardware-mcp 2026-08-24. See check_chip_identity."""
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        cutoff = as_of or "9999-12-31"
        qubit_vals = {}
        for prop in ("T1", "T2", "readout_error"):
            rows = cur.execute(
                """
                SELECT qubit_index, value FROM qubit_snapshots
                WHERE device_name = ? AND property_name = ? AND vendor_measured_at <= ?
                AND id IN (
                    SELECT MAX(id) FROM qubit_snapshots
                    WHERE device_name = ? AND property_name = ? AND vendor_measured_at <= ?
                    GROUP BY qubit_index
                )
                """,
                (device_name, prop, cutoff, device_name, prop, cutoff),
            ).fetchall()
            for qi, val in rows:
                qubit_vals.setdefault(qi, {})[prop.lower()] = val

        gate_rows = cur.execute(
            """
            SELECT qubit1, qubit2, value FROM pair_snapshots
            WHERE device_name = ? AND property_name = 'gate_error' AND vendor_measured_at <= ?
            AND id IN (
                SELECT MAX(id) FROM pair_snapshots
                WHERE device_name = ? AND property_name = 'gate_error' AND vendor_measured_at <= ?
                GROUP BY qubit1, qubit2
            )
            """,
            (device_name, cutoff, device_name, cutoff),
        ).fetchall()
        gate_errors_by_qubit = {}
        for q1, q2, val in gate_rows:
            gate_errors_by_qubit.setdefault(q1, []).append(val)
            gate_errors_by_qubit.setdefault(q2, []).append(val)
        for qi, errs in gate_errors_by_qubit.items():
            qubit_vals.setdefault(qi, {})["avg_gate_error"] = sum(errs) / len(errs)
    return qubit_vals


def audit_calibration_telemetry(device_name: str) -> dict:
    """
    Real bug #5 (the exact one this project just fixed — every calibration
    field silently null for 72 rows) generalized into a reusable, ongoing
    check: is the calibration DATA ITSELF trustworthy, not just "is the
    device healthy." verify()'s hardware_aware_simulation trusts this feed
    as ground truth; this audits the feed the same discipline the rest of
    this tool applies to circuits. Added 2026-08-24.

    Four checks, all on real device_snapshots/qubit_snapshots history:
      1. Frozen values — a parameter identical across the last N real
         readings when its own longer history shows real variance.
      2. Suspicious placeholder values — exact round numbers unlikely to
         be a real physical measurement (e.g. exactly 0.5, exactly 0).
      3. Physics-invariant violation — T2 > 2*T1 is not physically
         possible; a violation is feed corruption, never real physics.
      4. Per-qubit copy-paste — many qubits reporting the identical value
         when the device's own history shows real per-qubit spread.

    Does NOT judge whether the device is healthy (get_alerts/the drift
    gate already do that) — only whether the numbers describing it look
    like real measurements.
    """
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        findings = []

        # --- Check 1 & 2: frozen values and placeholders, on device_snapshots ---
        for prop in ("avg_cx_error", "avg_readout_error", "median_t1_us", "median_t2_us"):
            rows = cur.execute(
                f"SELECT ts, {prop} FROM device_snapshots WHERE name = ? AND {prop} IS NOT NULL "
                f"ORDER BY ts DESC LIMIT 20",
                (device_name,),
            ).fetchall()
            values = [v for _, v in rows]
            if len(values) >= 5:
                recent = values[:5]
                if len(set(recent)) == 1 and len(set(values)) > 1:
                    findings.append({
                        "check": "frozen_value", "property": prop,
                        "message": f"{prop} has been exactly {recent[0]} for the last 5 real "
                                   f"readings, but this device's own longer history shows real "
                                   f"variance ({len(set(values))} distinct values in last 20).",
                    })
            for ts, v in rows[:5]:
                if v is not None and v in (0.0, 0.5, 1.0) :
                    findings.append({
                        "check": "suspicious_placeholder", "property": prop, "ts": ts,
                        "message": f"{prop}={v} at {ts} — a suspiciously round number for a "
                                   "real physical measurement, worth checking against the vendor's raw record.",
                    })

        # --- Check 3: physics invariant, T2 <= 2*T1 ---
        row = cur.execute(
            "SELECT ts, median_t1_us, median_t2_us FROM device_snapshots "
            "WHERE name = ? AND median_t1_us IS NOT NULL AND median_t2_us IS NOT NULL "
            "ORDER BY ts DESC LIMIT 1",
            (device_name,),
        ).fetchone()
        if row:
            ts, t1, t2 = row
            if t1 and t2 and t2 > 2 * t1:
                findings.append({
                    "check": "physics_invariant_violation", "ts": ts,
                    "message": f"T2 ({t2}us) > 2*T1 ({2*t1}us) at {ts} — not physically possible. "
                               "This is feed corruption, never real physics.",
                })

        # --- Check 4: per-qubit copy-paste, using the new archive ---
        latest_ts = cur.execute(
            "SELECT MAX(vendor_measured_at) FROM qubit_snapshots WHERE device_name = ? AND property_name = 'T1'",
            (device_name,),
        ).fetchone()[0]
        if latest_ts:
            t1_vals = [v for (v,) in cur.execute(
                "SELECT value FROM qubit_snapshots WHERE device_name = ? AND property_name = 'T1' "
                "AND vendor_measured_at = ?",
                (device_name, latest_ts),
            ).fetchall()]
            if len(t1_vals) >= 10:
                distinct = len(set(t1_vals))
                if distinct <= max(1, len(t1_vals) // 10):
                    findings.append({
                        "check": "per_qubit_copy_paste", "ts": latest_ts,
                        "message": f"Only {distinct} distinct T1 value(s) across {len(t1_vals)} qubits "
                                   f"at {latest_ts} — real chips have real per-qubit spread; this "
                                   "looks like an aggregation bug, not real uniformity.",
                    })

    trust_score = max(0.0, 1.0 - 0.25 * len(findings))
    return {
        "device": device_name,
        "findings": findings,
        "n_findings": len(findings),
        "telemetry_trust_score": round(trust_score, 2),
        "verdict": "trust" if not findings else "review_before_trusting",
        "note": "This audits whether the calibration DATA looks real, not whether the device "
                "is healthy — a device can be genuinely unhealthy and still pass this audit, "
                "or be healthy with a broken feed. Both matter, separately.",
    }


def check_chip_identity(device_name: str, compare_days_back: int = 7) -> dict:
    """
    Detects a silent hardware swap or qubit relabeling via real per-qubit
    fingerprint correlation. Ported from quantum-hardware-mcp 2026-08-24,
    with one HONEST difference: the main tool's version compares against a
    threshold empirically calibrated from 831 real days of its own
    history. This project's per-qubit archive only started collecting
    today, so there isn't enough real history here yet to do the same
    calibration study — using a simple, conservative fixed threshold
    (0.5 raw correlation) instead, clearly labeled as provisional, not a
    borrowed or invented precise number. Re-run the same empirical study
    once this project has real historical depth (see
    quantum-hardware-mcp/README.md's "Per-qubit/per-pair calibration
    archive" entry for the method), and replace this threshold then.

    IBM does not expose per-qubit frequency for current-generation Heron
    devices — same limitation as the main tool's version. Uses T1/T2/
    readout/gate-error patterns instead, a real but noisier signal.
    """
    from scipy import stats

    with sqlite3.connect(DB_PATH) as con:
        earliest = con.execute(
            "SELECT MIN(vendor_measured_at) FROM qubit_snapshots WHERE device_name = ?",
            (device_name,),
        ).fetchone()[0]
    if not earliest:
        return {
            "error": f"No per-qubit history for '{device_name}' yet.",
            "hint": "Call get_device_details at least twice, hours or days apart, to build "
                    "enough history to compare.",
        }

    ref_date = (datetime.now(timezone.utc) - timedelta(days=compare_days_back)).isoformat()
    current = _qubit_fingerprint_vector(device_name)
    reference = _qubit_fingerprint_vector(device_name, as_of=ref_date)

    if len(reference) < 5:
        return {
            "error": f"Not enough history {compare_days_back} days back to compare "
                     f"(only {len(reference)} qubits found). Earliest data: {earliest}.",
            "hint": "This project's archive is new — try again once more history has accumulated.",
        }

    common_qubits = sorted(set(current) & set(reference))
    props = ["t1", "t2", "readout_error", "avg_gate_error"]
    per_property = {}
    for prop in props:
        cur_vals, ref_vals = [], []
        for qi in common_qubits:
            if prop in current.get(qi, {}) and prop in reference.get(qi, {}):
                cur_vals.append(current[qi][prop])
                ref_vals.append(reference[qi][prop])
        if len(cur_vals) < 5:
            continue
        corr, _ = stats.spearmanr(cur_vals, ref_vals)
        if corr == corr:  # skip NaN
            per_property[prop] = {"raw_correlation": round(corr, 4), "n_qubits": len(cur_vals)}

    if not per_property:
        return {"error": "No overlapping properties with enough data to compare."}

    avg_raw = sum(p["raw_correlation"] for p in per_property.values()) / len(per_property)
    PROVISIONAL_THRESHOLD = 0.5
    verdict = "consistent" if avg_raw >= PROVISIONAL_THRESHOLD else "possible_hardware_change_or_insufficient_data"

    return {
        "device": device_name,
        "compare_days_back": compare_days_back,
        "reference_earliest_available": earliest,
        "per_property": per_property,
        "avg_raw_correlation": round(avg_raw, 4),
        "threshold_used": PROVISIONAL_THRESHOLD,
        "verdict": verdict,
        "caveat": "PROVISIONAL threshold — this project's archive is too new for a real "
                  "gap-calibrated baseline (see quantum-hardware-mcp's version for that method "
                  "once this project has enough history). Treat a borderline result as genuinely "
                  "inconclusive, not confirmed.",
    }


def start_repro_experiment(circuit: str, backend_name: str, n_runs: int = 5, shots: int = 1024) -> dict:
    """Submit the same circuit N times to measure reproducibility on real hardware."""
    try:
        service = _get_service()
        backend = service.backend(backend_name)
        try:
            qc = QuantumCircuit.from_qasm_str(circuit)
        except Exception:
            qc = qiskit_qasm3.loads(circuit)
        pm = generate_preset_pass_manager(backend=backend, optimization_level=1)
        isa_circuit = pm.run(qc)
        props = backend.properties()
        cx_errors = _cx_errors_for_backend(props) if props else []
        calibration_epoch = round(sum(cx_errors) / len(cx_errors), 5) if cx_errors else None
        ts = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(DB_PATH) as con:
            cur = con.execute("""
                INSERT INTO repro_experiments (created_ts, device_name, circuit, n_runs, shots, status)
                VALUES (?, ?, ?, ?, ?, 'running')
            """, (ts, backend_name, circuit, n_runs, shots))
            experiment_id = cur.lastrowid
            sampler = Sampler(backend)
            job_ids = []
            for i in range(n_runs):
                job = sampler.run([isa_circuit], shots=shots)
                job_id = job.job_id()
                job_ids.append(job_id)
                con.execute("""
                    INSERT INTO repro_runs (experiment_id, run_index, submitted_ts, job_id, status, calibration_epoch)
                    VALUES (?, ?, ?, ?, 'submitted', ?)
                """, (experiment_id, i, datetime.now(timezone.utc).isoformat(), job_id,
                      str(calibration_epoch) if calibration_epoch else None))
        return {
            "experiment_id": experiment_id, "device": backend_name, "n_runs": n_runs,
            "shots": shots, "job_ids": job_ids, "calibration_epoch": calibration_epoch,
        }
    except Exception as e:
        return {"error": str(e)}


def repro_score(experiment_id: int) -> dict:
    """0-1 reproducibility score after repeat runs complete."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            exp = con.execute("""
                SELECT device_name, circuit, n_runs, shots, created_ts
                FROM repro_experiments WHERE id = ?
            """, (experiment_id,)).fetchone()
            if not exp:
                return {"error": f"Experiment {experiment_id} not found."}
            device_name = exp[0]
            runs = con.execute("""
                SELECT run_index, job_id, status, counts, calibration_epoch
                FROM repro_runs WHERE experiment_id = ? ORDER BY run_index
            """, (experiment_id,)).fetchall()
        service = _get_service()
        all_counts, pending, epochs = [], [], set()
        for run_index, job_id, status, counts_str, epoch in runs:
            if epoch:
                epochs.add(epoch)
            if counts_str:
                import json as _json
                all_counts.append(_json.loads(counts_str))
                continue
            if not job_id:
                pending.append(run_index)
                continue
            try:
                job = service.job(job_id)
                if str(job.status()) in ("JobStatus.DONE", "DONE", "done"):
                    result = job.result()
                    pub_result = result[0]
                    bitarray = pub_result.data
                    field = list(vars(bitarray).keys())[0] if vars(bitarray) else None
                    counts = getattr(bitarray, field).get_counts() if field else {}
                    import json as _json
                    with sqlite3.connect(DB_PATH) as con:
                        con.execute("UPDATE repro_runs SET status='done', counts=? WHERE experiment_id=? AND run_index=?",
                                    (_json.dumps(counts), experiment_id, run_index))
                    all_counts.append(counts)
                else:
                    pending.append(run_index)
            except Exception:
                pending.append(run_index)
        if pending:
            return {"experiment_id": experiment_id, "device": device_name, "status": "incomplete",
                    "completed_runs": len(all_counts), "pending_runs": pending}
        all_keys = set()
        for c in all_counts:
            all_keys.update(c.keys())
        dists = []
        for c in all_counts:
            total = sum(c.values()) or 1
            dists.append({k: c.get(k, 0) / total for k in all_keys})
        mean_dist = {k: sum(d[k] for d in dists) / len(dists) for k in all_keys}
        eps = 1e-10
        kl_divs = [round(sum(d[k] * math.log((d[k] + eps) / (mean_dist[k] + eps))
                              for k in all_keys if d[k] > 0), 6) for d in dists]
        avg_kl = sum(kl_divs) / len(kl_divs)
        score = round(max(0.0, 1.0 - (avg_kl / 0.5)), 3)
        top_bitstring = max(mean_dist, key=mean_dist.get)
        with sqlite3.connect(DB_PATH) as con:
            con.execute("UPDATE repro_experiments SET status='complete' WHERE id=?", (experiment_id,))
        verdict = ("RELIABLE" if score >= 0.9 else "MARGINAL" if score >= 0.7 else "UNRELIABLE")
        return {
            "experiment_id": experiment_id, "device": device_name, "n_runs": len(all_counts),
            "reproducibility_score": score, "verdict": verdict, "top_bitstring": top_bitstring,
            "top_bitstring_mean_probability": round(mean_dist[top_bitstring], 4),
            "avg_kl_divergence": round(avg_kl, 6),
            "calibration_drifted_between_runs": len(epochs) > 1,
        }
    except Exception as e:
        return {"error": str(e)}


def job_analytics() -> dict:
    """Breakdown of jobs submitted through this project, by tool."""
    if not os.path.exists(DB_PATH):
        return {"error": "No local database found yet."}
    try:
        with sqlite3.connect(DB_PATH) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT tool_name, COUNT(*) AS total_submissions,
                    AVG(circuit_depth_raw) AS avg_raw_depth,
                    AVG(circuit_depth_transpiled) AS avg_transpiled_depth,
                    AVG(CAST(circuit_depth_transpiled AS REAL) / NULLIF(circuit_depth_raw, 0)) AS expansion_ratio,
                    AVG(shots_requested) AS avg_shots
                FROM job_submissions GROUP BY tool_name ORDER BY total_submissions DESC
            """).fetchall()
            total_jobs = con.execute("SELECT COUNT(*) FROM job_submissions").fetchone()[0]
        if not rows:
            return {"total_jobs": 0, "message": "No jobs logged yet."}
        by_tool = {
            r["tool_name"]: {
                "total_submissions": r["total_submissions"],
                "avg_circuit_depth_raw": round(r["avg_raw_depth"], 1) if r["avg_raw_depth"] else None,
                "avg_circuit_depth_transpiled": round(r["avg_transpiled_depth"], 1) if r["avg_transpiled_depth"] else None,
                "transpilation_expansion_ratio": round(r["expansion_ratio"], 2) if r["expansion_ratio"] else None,
                "avg_shots": round(r["avg_shots"], 0) if r["avg_shots"] else None,
            }
            for r in rows
        }
        return {"total_jobs_logged": total_jobs, "by_tool": by_tool}
    except Exception as e:
        return {"error": str(e)}


def ibm_account_check() -> dict:
    """
    Which IBM Quantum instance(s) this account can access and real usage
    quota status. IonQ's equivalent (ionq_account_check) reports a dollar
    budget; IBM's real model is genuinely different, not just re-labeled —
    checked directly against the live API rather than assumed: the free
    "open" plan is a TIME quota (seconds of QPU access over a rolling
    period), not a dollar amount. Built to match what IBM's API actually
    exposes, not to force IonQ's shape onto it.
    """
    try:
        service = _get_service()
        instances = service.instances()
        usage = service.usage()
        remaining = usage.get("usage_remaining_seconds")
        return {
            "instances": instances,
            "usage": {
                "seconds_used": usage.get("usage_consumed_seconds"),
                "seconds_limit": usage.get("usage_limit_seconds"),
                "seconds_remaining": remaining,
                "limit_reached": usage.get("usage_limit_reached"),
                "period": usage.get("usage_period"),
            },
            "zero_quota_warning": bool(remaining is not None and remaining <= 0),
        }
    except Exception as e:
        return {"error": str(e)}


def _check_ibm_quota_before_submitting(backend_name: str, circuit, shots: int) -> dict:
    """
    Refuses submission with a clear reason if a circuit's estimated
    runtime exceeds the account's actual remaining time quota — the IBM
    equivalent of the IonQ dollar-budget preflight check, built after
    IonQ's version caught two real failures this project hit. Uses
    estimate_runtime's own estimate, converted from minutes to seconds to
    match IBM's real quota unit.
    """
    try:
        service = _get_service()
        backend = service.backend(backend_name)
        est = _estimate_minutes(backend, circuit, shots)
        estimated_seconds = est.get("total_estimate_mins", 0) * 60
        account = ibm_account_check()
        if "error" in account:
            return {"error": None}  # can't check -- don't block on this failing
        remaining = account["usage"]["seconds_remaining"]
        if remaining is not None and estimated_seconds > remaining:
            return {
                "error": f"Estimated runtime (~{estimated_seconds:.0f}s) exceeds remaining "
                         f"quota ({remaining}s) on the free/open plan.",
                "hint": "Wait for the quota period to reset, or use a different instance "
                        "if you have paid access, then resubmit.",
                "estimated_seconds": round(estimated_seconds, 1), "seconds_remaining": remaining,
            }
        return {"error": None}
    except Exception:
        return {"error": None}  # can't check -- don't block submission on this failing
