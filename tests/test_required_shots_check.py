"""
Tests for required_shots_check (core/verifier.py), added 2026-08-24 —
a real, concrete statistical power check: is a claimed amplification even
distinguishable from noise at the requested shot count, computed BEFORE
any simulation or real hardware time is spent.

Pure math, no live calls, no hardware.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.verifier as v


def test_amplification_of_one_is_not_applicable():
    """expected_amplification <= 1x claims no real effect over baseline —
    the power check doesn't mean anything for a null claim."""
    result = v.required_shots_check(n_marked=1, expected_amplification=1.0, n_qubits=2, requested_shots=100)
    assert result["applicable"] is False


def test_amplification_below_one_is_not_applicable():
    result = v.required_shots_check(n_marked=1, expected_amplification=0.5, n_qubits=2, requested_shots=100)
    assert result["applicable"] is False


def test_dramatic_effect_needs_very_few_shots():
    """A huge claimed effect (10x on a 2-qubit Bell-style claim) should be
    checkable with a tiny number of shots — sanity bound, not an exact
    hand-verified number."""
    result = v.required_shots_check(n_marked=1, expected_amplification=10.0, n_qubits=2, requested_shots=512)
    assert result["applicable"] is True
    assert result["required_shots"] < 50
    assert result["passed"] is True


def test_tiny_effect_needs_many_shots_and_fails_at_low_shots():
    """A barely-above-baseline claim (1.05x) on a circuit with a huge state
    space (20 qubits -> baseline probability ~1e-6) needs a very large
    number of shots to be distinguishable from pure noise — nowhere near
    achievable at a modest shot count."""
    result = v.required_shots_check(n_marked=1, expected_amplification=1.05, n_qubits=20, requested_shots=4096)
    assert result["applicable"] is True
    assert result["required_shots"] > 4096
    assert result["passed"] is False
    assert "not enough" in result["verdict"].lower()


def test_required_shots_matches_requested_boundary():
    """Sanity check the pass/fail boundary is exactly requested >= required,
    not off-by-one in either direction."""
    result = v.required_shots_check(n_marked=1, expected_amplification=3.0, n_qubits=3, requested_shots=1_000_000)
    assert result["passed"] is True
    result2 = v.required_shots_check(n_marked=1, expected_amplification=3.0, n_qubits=3, requested_shots=1)
    assert result2["passed"] is False


def test_amplification_clamped_at_full_certainty():
    """expected_amplification so large it implies p1 > 1 (impossible for a
    probability) should clamp to 1.0, not silently produce nonsense."""
    result = v.required_shots_check(n_marked=1, expected_amplification=100.0, n_qubits=1, requested_shots=10)
    assert result["applicable"] is True
    assert result["claimed_probability"] == 1.0


# ---------------------------------------------------------------------------
# Integration: verify() blocks an unfalsifiable claim before simulating
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


def test_verify_blocks_unfalsifiable_claim_before_simulating(monkeypatch):
    """A claim that couldn't be distinguished from noise even if true should
    BLOCK immediately, and hardware_aware_simulation should never even run —
    the whole point is not wasting compute/money on an unfalsifiable claim."""
    def _boom(*a, **k):
        raise AssertionError("hardware_aware_simulation must not run when the claim is unfalsifiable")
    monkeypatch.setattr(v, "hardware_aware_simulation", _boom)

    result = v.verify(BELL, provider="ionq", target_device="simulator", shots=1,
                       expected_marked_bitstrings=["00", "11"], expected_amplification=1.01)
    assert result["verdict"] == "BLOCK"
    assert "required_shots_check" in result
    assert result["required_shots_check"]["passed"] is False


def test_verify_includes_power_check_when_claim_is_checkable():
    result = v.verify(BELL, provider="ionq", target_device="simulator", shots=1024,
                       expected_marked_bitstrings=["00", "11"], expected_amplification=2.0)
    assert "required_shots_check" in result
    assert result["required_shots_check"]["applicable"] is True
    assert result["required_shots_check"]["passed"] is True
