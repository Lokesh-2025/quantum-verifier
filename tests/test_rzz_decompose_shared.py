"""
Regression test for the RZZ-decompose duplication fix.

core/verifier.py used to carry its own hand-typed, byte-identical copy of
_decompose_large_angle_rzz / _register_ionq_native_equivalences alongside
providers/ionq.py's copy, with nothing enforcing the two stayed in sync.
core/verifier.py now imports both directly from providers/ionq.py instead.

This test has no external dependencies (no API key, no network, no
hardware) and should always run — it fails loudly if anyone reintroduces
a second, independent copy of either function.
"""
import core.verifier as verifier
import providers.ionq as ionq


def test_decompose_large_angle_rzz_is_shared():
    assert verifier._decompose_large_angle_rzz is ionq._decompose_large_angle_rzz


def test_register_ionq_native_equivalences_is_shared():
    assert verifier._register_ionq_native_equivalences is ionq._register_ionq_native_equivalences
