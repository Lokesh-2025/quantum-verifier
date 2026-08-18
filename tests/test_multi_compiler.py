"""
Tests for the multi-compiler diff engine (core/multi_compiler.py).

Live IBM account calls (transpile-only, no job submission, no cost) —
skips automatically if IBM_QUANTUM_TOKEN isn't configured, same pattern
as tests/test_ibm_tooling.py.
"""
import os
import pytest
from dotenv import load_dotenv

load_dotenv()
IBM_TOKEN_PRESENT = bool(os.getenv("IBM_QUANTUM_TOKEN"))
pytestmark = pytest.mark.skipif(
    not IBM_TOKEN_PRESENT, reason="IBM_QUANTUM_TOKEN not set — skipping live IBM multi-compiler tests"
)

from core.multi_compiler import diff_compilers

GHZ3 = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
creg c[3];
h q[0];
cx q[0],q[1];
cx q[1],q[2];
h q[2];
cx q[0],q[2];
measure q[0] -> c[0];
measure q[1] -> c[1];
measure q[2] -> c[2];
"""


def test_both_compilers_produce_verified_results_for_a_real_circuit():
    result = diff_compilers(GHZ3, "ibm_fez")
    assert "error" not in result
    assert result["results"]["qiskit"]["verified"] is True
    assert result["results"]["tket"]["verified"] is True
    assert result["recommendation"]  # some non-empty recommendation was produced


def test_recommendation_picks_the_lower_two_qubit_gate_count_among_verified():
    result = diff_compilers(GHZ3, "ibm_fez")
    q, t = result["results"]["qiskit"], result["results"]["tket"]
    winner = "qiskit" if q["two_qubit_gates"] <= t["two_qubit_gates"] else "tket"
    assert winner in result["recommendation"]


def test_bad_ibm_device_reports_its_own_error_without_crashing():
    result = diff_compilers(GHZ3, "not-a-real-ibm-device")
    assert "error" in result


def test_measurements_are_stripped_and_noted_for_comparison():
    result = diff_compilers(GHZ3, "ibm_fez")
    assert result["measurements_stripped_for_comparison"] is True
