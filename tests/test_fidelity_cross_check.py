"""
Tests for cross_check_fidelity_estimate (core/verifier.py), added
2026-08-24 — applies the "verify via independent method" principle
already used by falsify_claim (real vs control) and diff_compilers
(Qiskit vs TKET) one level deeper, to hardware_aware_simulation's own
noise estimate: IBM's path already computes an analytical estimate AND a
full noisy simulation, but never compared them until now.

Pure function of a QuantumCircuit + a hand-constructed hw_result dict —
no live IBM calls, since ideal_simulation() (used internally) is a local
Aer simulation with no external dependency.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from qiskit import QuantumCircuit

import core.verifier as v


def _bell_circuit():
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure([0, 1], [0, 1])
    return qc


def test_not_applicable_when_counts_missing():
    result = v.cross_check_fidelity_estimate(_bell_circuit(), {"counts": None, "estimated_fidelity": 0.9})
    assert result["applicable"] is False


def test_not_applicable_when_estimate_missing():
    result = v.cross_check_fidelity_estimate(_bell_circuit(), {"counts": {"00": 100}, "estimated_fidelity": None})
    assert result["applicable"] is False


def test_perfect_agreement_when_noisy_matches_ideal_and_estimate_is_high():
    """A noiseless-looking result (counts matching the ideal Bell
    distribution almost exactly) should agree with a high analytical
    estimate — no disagreement flagged."""
    hw_result = {
        "counts": {"00": 2048, "11": 2048},  # matches real Bell ideal distribution closely
        "estimated_fidelity": 0.98,
    }
    result = v.cross_check_fidelity_estimate(_bell_circuit(), hw_result, shots=4096)
    assert result["applicable"] is True
    assert result["significant_disagreement"] is False


def test_flags_real_disagreement_between_methods():
    """A wildly noisy result (uniform over all 4 bitstrings) against a
    confidently high analytical estimate should be flagged — the two
    methods substantially disagree."""
    hw_result = {
        "counts": {"00": 1024, "01": 1024, "10": 1024, "11": 1024},  # uniform -- no real signal
        "estimated_fidelity": 0.95,  # analytical method confidently says "should be fine"
    }
    result = v.cross_check_fidelity_estimate(_bell_circuit(), hw_result, shots=4096)
    assert result["applicable"] is True
    assert result["significant_disagreement"] is True
    assert "disagree" in result["verdict"].lower()


def test_disagreement_is_symmetric_absolute_difference():
    hw_result = {"counts": {"00": 2048, "11": 2048}, "estimated_fidelity": 0.1}
    result = v.cross_check_fidelity_estimate(_bell_circuit(), hw_result, shots=4096)
    assert result["disagreement"] == round(abs(result["simulated_overlap_fidelity"] - 0.1), 4)


# ---------------------------------------------------------------------------
# Integration: verify() includes this only for the IBM path
# ---------------------------------------------------------------------------

def test_verify_does_not_run_cross_check_for_ionq(monkeypatch):
    """cross_check_fidelity_estimate is IBM-specific (IonQ's path doesn't
    produce the same paired analytical+simulated signals) -- verify()
    should skip it entirely for provider='ionq'."""
    def _fake_hw(circuit, provider, target_device, shots):
        return {"counts": {"00": 100}, "total_shots": 100}

    monkeypatch.setattr(v, "hardware_aware_simulation", _fake_hw)

    def _boom(*a, **k):
        raise AssertionError("cross_check_fidelity_estimate must not run for provider='ionq'")
    monkeypatch.setattr(v, "cross_check_fidelity_estimate", _boom)

    result = v.verify(
        'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\nh q[0];\ncx q[0],q[1];\n'
        "measure q[0] -> c[0];\nmeasure q[1] -> c[1];\n",
        provider="ionq", target_device="simulator", shots=100,
    )
    assert "fidelity_cross_check" not in result
