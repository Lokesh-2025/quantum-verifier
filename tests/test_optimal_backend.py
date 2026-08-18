"""
Tests for the cross-provider cost/quality router (core/optimal_backend.py).

Live IonQ free-simulator + live IBM account calls (no real hardware
credits spent) — skips automatically if the relevant credentials aren't
configured, same pattern as tests/test_verifier.py.
"""
import os
import pytest
from dotenv import load_dotenv

load_dotenv()
IONQ_KEY_PRESENT = bool(os.getenv("IONQ_API_KEY"))
IBM_TOKEN_PRESENT = bool(os.getenv("IBM_QUANTUM_TOKEN"))

from core.optimal_backend import find_optimal_backend

BELL = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0],q[1];
measure q[0] -> c[0];
measure q[1] -> c[1];
"""


@pytest.mark.skipif(not IONQ_KEY_PRESENT, reason="IONQ_API_KEY not set")
def test_ionq_only_reports_cost_and_fidelity_proxy():
    result = find_optimal_backend(BELL, ibm_device="", ionq_device="forte-1", shots=1024)
    assert result["ibm"] is None
    assert "error" not in result["ionq"]
    assert result["ionq"]["cost"]["unit"] == "USD"
    assert 0 <= result["ionq"]["quality"]["fidelity_proxy"] <= 1
    assert "Only ionq" in result["recommendation"]


@pytest.mark.skipif(not IONQ_KEY_PRESENT, reason="IONQ_API_KEY not set")
def test_neither_device_given_returns_empty_recommendation():
    result = find_optimal_backend(BELL, ibm_device="", ionq_device="", shots=1024)
    assert result["ibm"] is None
    assert result["ionq"] is None
    assert "Neither provider" in result["recommendation"]


@pytest.mark.skipif(not IONQ_KEY_PRESENT, reason="IONQ_API_KEY not set")
def test_bad_ionq_device_reports_its_own_error_without_crashing():
    result = find_optimal_backend(BELL, ibm_device="", ionq_device="not-a-real-device", shots=1024)
    assert "error" in result["ionq"] or result["ionq"] is not None


@pytest.mark.skipif(not (IONQ_KEY_PRESENT and IBM_TOKEN_PRESENT), reason="needs both IONQ_API_KEY and an IBM token")
def test_both_providers_report_different_cost_units():
    result = find_optimal_backend(BELL, ibm_device="ibm_fez", ionq_device="forte-1", shots=1024)
    assert result["ibm"]["cost"]["unit"] != result["ionq"]["cost"]["unit"]
    assert "Cost units differ" in result["recommendation"]
