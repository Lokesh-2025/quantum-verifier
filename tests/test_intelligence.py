"""
Test coverage for core/intelligence.py — the third deferred layer
(Memory, Postmortem, Intelligence) from the original plan, built only
after Memory had real data to draw from.

Isolated to a temp db by tests/conftest.py's session-wide autouse fixture
(added 2026-08-27, after an audit found this file's OWN fixture data --
source="unit_test", predicted=10.0/real=8.0, repeated on every prior
uninstalled test run -- had silently accumulated to 42 of 45 rows in the
REAL predictions table, corrupting verdict_track_record()'s live "how
trustworthy is this tool" number for weeks). Uses source="verify_experiment"
(the real default) rather than "unit_test" now: core.memory._NON_REAL_SOURCES
excludes "unit_test" specifically so memory_summary()/verdict_track_record()
never again average in synthetic fixture data, and this file's data is
genuinely isolated (its own temp db) so there's no real provenance lie in
using the real-looking source label here.
"""
from core.intelligence import recommend_tolerance, MIN_DATA_POINTS_FOR_RECOMMENDATION
from core.memory import record_prediction, record_real_result


def test_recommend_tolerance_falls_back_honestly_with_no_data():
    result = recommend_tolerance("ibm", "a_device_with_no_history_yet", default=0.5)
    assert result["recommended_tolerance"] == 0.5
    assert result["n_real_data_points"] == 0
    assert "not enough" in result["confidence"].lower()


def test_recommend_tolerance_uses_real_data_once_enough_exists():
    """Seed a controlled, isolated set of predictions with a KNOWN error
    rate and confirm the recommendation reflects it, not a guess."""
    provider, device = "ibm", "test_device_for_intelligence_unit_test"
    fake_qasm_base = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[1];\ncreg c[1];\nh q[0];\nmeasure q[0]->c[0];\n'

    # 3 predictions, each off by exactly 20% -- known, controlled error rate
    for i in range(MIN_DATA_POINTS_FOR_RECOMMENDATION):
        pred = record_prediction(
            fake_qasm_base + f"// variant {i}\n", provider=provider, target_device=device,
            predicted_amplification=10.0, marked_bitstrings=["0"], source="verify_experiment",
        )
        record_real_result(pred["prediction_id"], real_amplification=8.0)  # 20% off

    result = recommend_tolerance(provider, device, default=0.5)
    assert result["n_real_data_points"] >= MIN_DATA_POINTS_FOR_RECOMMENDATION
    assert abs(result["observed_mean_relative_error"] - 0.25) < 0.01  # |10-8|/8 = 0.25
    # recommended tolerance must sit comfortably above the observed error, not equal to it
    assert result["recommended_tolerance"] > result["observed_mean_relative_error"]
    assert result["confidence"] != "default — not enough real data yet"
