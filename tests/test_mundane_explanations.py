"""
Tests for the mundane-explanations checks (core/verifier.py), added
2026-08-27 overnight — boring, non-quantum explanations for an apparent
failure, checked before concluding hardware/noise/entanglement did
something surprising. Each check here is verified against a real,
reproduced bug scenario, not assumed to be a real failure mode.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator

import core.verifier as v


# ---------------------------------------------------------------------------
# detect_reversed_bitstring_convention
# ---------------------------------------------------------------------------

def test_reversed_convention_reproduces_the_real_qiskit_behavior_first():
    """Not an assumption -- confirmed live: an X gate on qubit 0 alone
    (qubit 1 left at |0>) must produce counts key '01', per Qiskit's real
    convention (qubit 0 = rightmost/least-significant character)."""
    qc = QuantumCircuit(2, 2)
    qc.x(0)
    qc.measure(0, 0)
    qc.measure(1, 1)
    counts = AerSimulator().run(qc, shots=200).result().get_counts()
    assert set(counts.keys()) == {"01"}


def test_wrong_bit_order_claim_is_flagged_suspicious():
    """The real bug: someone claims '10' (assuming string-index==qubit-
    index) when the real, correct claim for this exact circuit is '01'.
    Must be flagged."""
    counts = {"01": 200}  # the real Qiskit output for X-on-qubit-0
    result = v.detect_reversed_bitstring_convention(["10"], counts)
    assert result["applicable"] is True
    assert result["suspected_reversed_bit_order"] is True
    assert result["reversed_match_rate"] == 1.0
    assert result["claimed_match_rate"] == 0.0


def test_correct_bit_order_claim_is_not_flagged():
    counts = {"01": 200}
    result = v.detect_reversed_bitstring_convention(["01"], counts)
    assert result["applicable"] is True
    assert result["suspected_reversed_bit_order"] is False


def test_palindromic_bitstring_is_not_applicable():
    """'0110' reversed is '0110' -- nothing to distinguish, must not
    false-flag on circuits where the claim is symmetric."""
    counts = {"0110": 100}
    result = v.detect_reversed_bitstring_convention(["0110"], counts)
    assert result["applicable"] is False


def test_genuinely_failed_claim_does_not_false_flag_as_reversed():
    """Neither orientation matches well -- a real failure, not a bit-order
    mistake. Must not claim 'suspicious' when the reversed version is
    ALSO a poor match."""
    counts = {"11": 100, "00": 100}
    result = v.detect_reversed_bitstring_convention(["01"], counts)
    assert result["suspected_reversed_bit_order"] is False


def test_not_applicable_when_no_counts_or_no_claim():
    assert v.detect_reversed_bitstring_convention(["01"], None)["applicable"] is False
    assert v.detect_reversed_bitstring_convention([], {"01": 100})["applicable"] is False
    assert v.detect_reversed_bitstring_convention(["01"], {"01": 0})["applicable"] is False


# ---------------------------------------------------------------------------
# detect_suspicious_register_mapping
# ---------------------------------------------------------------------------

def test_identity_mapping_confirmed_via_real_circuit_introspection():
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.measure(0, 0)
    qc.measure(1, 1)
    result = v.detect_suspicious_register_mapping(qc)
    assert result["applicable"] is True
    assert result["is_identity_mapping"] is True
    assert result["measure_mapping"] == [(0, 0), (1, 1)]


def test_swapped_mapping_is_detected_via_real_circuit_introspection():
    """The real bug: measure(0)->c[1], measure(1)->c[0] -- legal QASM,
    silently wrong if read as identity. Confirmed via real
    circuit.find_bit introspection, not assumed."""
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.measure(0, 1)
    qc.measure(1, 0)
    result = v.detect_suspicious_register_mapping(qc)
    assert result["applicable"] is True
    assert result["is_identity_mapping"] is False
    assert result["measure_mapping"] == [(0, 1), (1, 0)]
    assert "WRONG" in result["verdict"]


def test_duplicate_clbit_mapping_is_flagged():
    qc = QuantumCircuit(2, 1)
    qc.h(0)
    qc.measure(0, 0)
    qc.measure(1, 0)  # overwrites qubit 0's result
    result = v.detect_suspicious_register_mapping(qc)
    assert result["applicable"] is True
    assert result["multiple_qubits_share_a_clbit"] is True


def test_no_measurements_is_not_applicable():
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    result = v.detect_suspicious_register_mapping(qc)
    assert result["applicable"] is False


# ---------------------------------------------------------------------------
# detect_stale_job_result
# ---------------------------------------------------------------------------

def test_matching_shot_count_reports_no_issue():
    result = v.detect_stale_job_result({"00": 500, "11": 500}, expected_total_shots=1000)
    assert result["shot_count_matches"] is True
    assert result["issues"] == []


def test_shot_count_mismatch_is_a_real_flagged_issue():
    """The real bug: a result whose counts sum to a DIFFERENT total than
    what was requested -- concrete evidence this result may not belong to
    this request at all."""
    result = v.detect_stale_job_result({"00": 100, "11": 100}, expected_total_shots=1000)
    assert result["shot_count_matches"] is False
    assert result["actual_total_shots"] == 200
    assert len(result["issues"]) == 1


def test_repeat_hash_over_threshold_is_flagged():
    result = v.detect_stale_job_result(
        {"00": 500, "11": 500}, expected_total_shots=1000,
        circuit_hash="abc123", previously_seen_hashes={"abc123": 5},
    )
    assert result["repeat_count"] == 5
    assert len(result["issues"]) == 1


def test_repeat_hash_under_threshold_is_not_flagged():
    result = v.detect_stale_job_result(
        {"00": 500, "11": 500}, expected_total_shots=1000,
        circuit_hash="abc123", previously_seen_hashes={"abc123": 1},
    )
    assert result["issues"] == []


def test_not_applicable_with_no_counts():
    assert v.detect_stale_job_result(None, expected_total_shots=1000)["applicable"] is False


# ---------------------------------------------------------------------------
# Integration: wired into verify(), added 2026-08-28 -- informational only,
# same "earn integration first" pattern ground_truth_significance_test used.
# Must appear in the output and must NEVER affect the GO/BLOCK verdict.
# ---------------------------------------------------------------------------

BELL = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q[0] -> c[0];
measure q[1] -> c[1];
""".strip()


def test_all_three_checks_appear_in_verify_output_without_changing_the_verdict(monkeypatch):
    def _fake_hw(circuit, provider, target_device, shots):
        return {"counts": {"00": 2048, "11": 2048}, "total_shots": 4096}

    monkeypatch.setattr(v, "hardware_aware_simulation", _fake_hw)

    result = v.verify(BELL, provider="ionq", target_device="simulator", shots=4096,
                       expected_marked_bitstrings=["00", "11"], expected_amplification=2.0)

    assert "register_mapping_check" in result
    assert "stale_result_check" in result
    assert "reversed_bitstring_check" in result
    assert result["register_mapping_check"]["applicable"] is True
    assert result["stale_result_check"]["applicable"] is True
    # ["00", "11"] are both palindromes -- correctly non-applicable here,
    # nothing to distinguish (see test_palindromic_bitstring_is_not_applicable)
    assert result["reversed_bitstring_check"]["applicable"] is False
    # old check still drives the actual verdict, unchanged
    assert result["verdict"] == "GO"


def test_register_mapping_check_appears_even_without_a_claim(monkeypatch):
    """Doesn't need expected_marked_bitstrings -- only needs the parsed
    circuit, so it must run even in discovery mode (no claim supplied)."""
    def _fake_hw(circuit, provider, target_device, shots):
        return {"counts": {"00": 2048, "11": 2048}, "total_shots": 4096}

    monkeypatch.setattr(v, "hardware_aware_simulation", _fake_hw)

    result = v.verify(BELL, provider="ionq", target_device="simulator", shots=4096)
    assert "register_mapping_check" in result
    assert result["register_mapping_check"]["is_identity_mapping"] is True
    # discovery mode -- reversed_bitstring_check needs a claim, must not appear
    assert "reversed_bitstring_check" not in result
