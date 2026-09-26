"""
Tests for the 2026-09-26 job-einstein redesign: core.verifier.wilson_score_interval
(extracted shared helper), core.intelligence.estimate_tolerance_band (the
decomposed statistical+systematic tolerance estimate), and
core.memory.record_tolerance_v2_comparison / tolerance_v2_disagreement_log
(the shadow-mode rollout log), following the exact pattern already proved
out for ground_truth_significance_test / shadow_mode_disagreement_log.

Isolated to a temp db by tests/conftest.py's session-wide autouse fixture,
same guarantee every other test file in this suite already has.
"""
import math

import pytest

import core.memory as memory
from core.intelligence import (
    CREDIBILITY_K, MIN_DATA_POINTS_FOR_RECOMMENDATION,
    _calibration_drift_proxy, estimate_tolerance_band,
)
from core.memory import record_prediction, record_real_result
from core.verifier import ground_truth_significance_test, wilson_score_interval


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_experiment_memory.db")
    monkeypatch.setattr(memory, "_DB_PATH", db_path)
    return db_path


# ---------------------------------------------------------------- wilson_score_interval

def test_wilson_score_interval_matches_ground_truth_significance_test_inline_math():
    """Behavior-preserving refactor check: the extracted helper must produce
    the exact same CI ground_truth_significance_test's own output already
    reports, on the same inputs."""
    hw_counts = {"111": 900, "000": 100}
    result = ground_truth_significance_test(hw_counts, ["111"], expected_amplification=3.5)
    ci_lo, ci_hi = wilson_score_interval(900, 1000, alpha=0.05)
    assert round(ci_lo, 6) == result["confidence_interval"]["lower"]
    assert round(ci_hi, 6) == result["confidence_interval"]["upper"]


def test_wilson_score_interval_handles_zero_total():
    lo, hi = wilson_score_interval(0, 0)
    assert (lo, hi) == (0.0, 1.0)


def test_wilson_score_interval_widens_with_fewer_shots():
    lo_small, hi_small = wilson_score_interval(50, 100)
    lo_large, hi_large = wilson_score_interval(5000, 10000)
    assert (hi_small - lo_small) > (hi_large - lo_large)


# ---------------------------------------------------------------- estimate_tolerance_band

def _hw_result(counts, n_two_qubit_gates=10):
    return {"counts": counts, "n_two_qubit_gates": n_two_qubit_gates}


def test_not_applicable_with_no_counts():
    result = estimate_tolerance_band("ibm", "some_device", {}, ["11"])
    assert result["applicable"] is False


def test_cold_start_with_no_history_and_no_calibration_data_falls_back_to_default():
    """No real prediction history AND providers.ibm.device_history has
    nothing to offer -- must fall back to the plain default, never a
    fabricated number."""
    hw = _hw_result({"11": 800, "00": 200})
    result = estimate_tolerance_band("ibm", "device_with_absolutely_nothing", hw, ["11"], default=0.5)
    assert result["applicable"] is True
    assert result["systematic_source"] == "default (no history, no calibration data)"
    assert result["systematic"] == 0.5
    assert result["n_real_data_points"] == 0
    # combined must be at least as large as either component alone (quadrature)
    assert result["tolerance"] >= result["statistical"]
    assert result["tolerance"] >= result["systematic"]


def test_calibration_drift_proxy_used_when_ibm_has_calibration_history_but_no_predictions(monkeypatch):
    """The real point of this pass: IBM should get a genuine, computed
    systematic estimate on day one from calibration data it already has,
    not the flat default -- confirmed via a controlled, fake device_history."""
    import providers.ibm as ibm_module

    def fake_device_history(device_name, days=7):
        return {"device": device_name, "days": days, "snapshots": [
            {"avg_cx_error": 0.01}, {"avg_cx_error": 0.015}, {"avg_cx_error": 0.008},
        ]}

    monkeypatch.setattr(ibm_module, "device_history", fake_device_history)

    hw = _hw_result({"11": 800, "00": 200}, n_two_qubit_gates=20)
    result = estimate_tolerance_band("ibm", "ibm_fake_device", hw, ["11"], default=0.5)
    assert result["systematic_source"] == "calibration_drift_proxy"
    assert result["systematic"] > 0
    assert result["n_real_data_points"] == 0


def test_calibration_drift_proxy_returns_none_with_fewer_than_two_snapshots(monkeypatch):
    import providers.ibm as ibm_module
    monkeypatch.setattr(ibm_module, "device_history",
                         lambda device_name, days=7: {"snapshots": [{"avg_cx_error": 0.01}]})
    assert _calibration_drift_proxy("ibm_fake_device", 10) is None


def test_calibration_drift_proxy_returns_none_without_gate_count():
    assert _calibration_drift_proxy("ibm_fake_device", None) is None
    assert _calibration_drift_proxy("ibm_fake_device", 0) is None


def test_historical_systematic_blends_smoothly_not_a_cliff(temp_db):
    """Buhlmann-style credibility must move CONTINUOUSLY as n grows, unlike
    recommend_tolerance's hard cutoff at MIN_DATA_POINTS_FOR_RECOMMENDATION."""
    provider, device = "ionq", "test_device_tolerance_v2"
    fake_qasm = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[1];\ncreg c[1];\nh q[0];\nmeasure q[0]->c[0];\n'

    def _seed(n, tag):
        for i in range(n):
            pred = record_prediction(
                fake_qasm + f"// {tag}-{i}\n", provider=provider, target_device=device,
                predicted_amplification=10.0, marked_bitstrings=["0"], source="verify_experiment",
            )
            record_real_result(pred["prediction_id"], real_amplification=8.0)  # 25% off, known

    hw = _hw_result({"1": 800, "0": 200})

    _seed(MIN_DATA_POINTS_FOR_RECOMMENDATION, "low")
    low_n_result = estimate_tolerance_band(provider, device, hw, ["1"], default=0.5)

    _seed(20, "high")  # push n well past the old cliff point
    high_n_result = estimate_tolerance_band(provider, device, hw, ["1"], default=0.5)

    assert low_n_result["systematic_source"].startswith("historical")
    assert high_n_result["n_real_data_points"] > low_n_result["n_real_data_points"]
    # More real data should move the systematic estimate further from the
    # arbitrary default and closer to the real observed historical error --
    # a smooth trend, not a discontinuous jump already locked in at n=3.
    historical_target = min(1.0, 0.25 * 1.5 + 0.1)
    assert abs(high_n_result["systematic"] - historical_target) < abs(low_n_result["systematic"] - historical_target)


def test_credibility_k_produces_partial_not_full_trust_at_old_cliff_point():
    """At n == MIN_DATA_POINTS_FOR_RECOMMENDATION (the old hard cutoff),
    credibility must be a real fraction, not 1.0 -- otherwise this is just
    the old cliff with new math."""
    n = MIN_DATA_POINTS_FOR_RECOMMENDATION
    credibility = n / (n + CREDIBILITY_K)
    assert 0 < credibility < 1.0


# ---------------------------------------------------------------- shadow-mode log

def _old_check(within, expected=3.5, observed=3.2):
    return {"applicable": True, "within_tolerance": within,
            "expected_amplification": expected, "observed_amplification": observed}


def _new_estimate(tolerance, statistical=0.1, systematic=0.2, source="calibration_drift_proxy"):
    return {"applicable": True, "tolerance": tolerance, "statistical": statistical,
            "systematic": systematic, "systematic_source": source}


def test_tolerance_v2_no_comparisons_logged_reports_zero_not_a_crash(temp_db):
    result = memory.tolerance_v2_disagreement_log()
    assert result["total_comparisons_logged"] == 0
    assert result["disagreement_count"] == 0


def test_tolerance_v2_agreement_logged_but_not_a_disagreement(temp_db):
    # expected=3.5, observed=3.2 within old tolerance and within new (both True)
    memory.record_tolerance_v2_comparison(
        "ibm", "ibm_fez", "OPENQASM 2.0;",
        _old_check(within=True), 0.5, "explicit", _new_estimate(tolerance=0.5))
    result = memory.tolerance_v2_disagreement_log()
    assert result["total_comparisons_logged"] == 1
    assert result["disagreement_count"] == 0


def test_tolerance_v2_disagreement_is_logged_with_full_detail(temp_db):
    # old tolerance very tight -> old check says NOT within tolerance;
    # new (wider) tolerance -> within. A real disagreement.
    memory.record_tolerance_v2_comparison(
        "ibm", "ibm_fez", "OPENQASM 2.0;",
        _old_check(within=False, expected=3.5, observed=3.2), 0.01, "explicit",
        _new_estimate(tolerance=0.5, statistical=0.12, systematic=0.3, source="calibration_drift_proxy"))
    result = memory.tolerance_v2_disagreement_log()
    assert result["total_comparisons_logged"] == 1
    assert result["disagreement_count"] == 1
    row = result["disagreements"][0]
    assert row["old_within_tolerance"] is False
    assert row["new_within_tolerance"] is True
    assert row["new_systematic_source"] == "calibration_drift_proxy"
    assert row["old_tolerance"] == 0.01
    assert row["old_tolerance_source"] == "explicit"


def test_tolerance_v2_non_applicable_checks_are_silently_skipped(temp_db):
    memory.record_tolerance_v2_comparison(
        "ibm", "ibm_fez", "OPENQASM 2.0;",
        {"applicable": False}, 0.5, "explicit", _new_estimate(tolerance=0.5))
    result = memory.tolerance_v2_disagreement_log()
    assert result["total_comparisons_logged"] == 0


def test_tolerance_v2_known_synthetic_source_excluded(temp_db):
    memory.record_tolerance_v2_comparison(
        "ibm", "ibm_fez", "OPENQASM 2.0;",
        _old_check(within=False), 0.01, "explicit", _new_estimate(tolerance=0.5), source="unit_test")
    result = memory.tolerance_v2_disagreement_log()
    assert result["total_comparisons_logged"] == 0
