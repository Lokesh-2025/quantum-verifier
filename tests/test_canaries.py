"""
Canary tests for IonQ correctness bugs that would NOT be caught by symmetric
test circuits (Bell states, GHZ chains).

Ported verbatim from quantum-hardware-mcp/tests/test_ionq_canaries.py — see
the approved plan for why these are the regression baseline for this
project's Verifier: they test exactly the class of silent, plausible-
looking-but-wrong bug the Verifier exists to catch.

Context: a real bug in this project's own history (qforge's Pauli-label
endianness bug) passed every existing test for weeks because the specific
test cases happened to be symmetric under exactly that bug. It was only
caught by testing an asymmetric case. These tests apply that lesson to the
IBM<->IonQ boundary: bit-ordering and angle-unit conversions are exactly
the kind of silent, plausible-looking-but-wrong bug that a Bell state
(0/1 symmetric) or GHZ chain (all-qubits-equal) structurally cannot expose.

No hardware required — runs entirely on IonQ's free simulator.
Skips automatically if IONQ_API_KEY isn't configured.
"""
import math
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

IONQ_KEY_PRESENT = bool(os.getenv("IONQ_API_KEY"))
pytestmark = pytest.mark.skipif(
    not IONQ_KEY_PRESENT, reason="IONQ_API_KEY not set — skipping live IonQ canary tests"
)


def _ionq_simulator_backend():
    from qiskit_ionq import IonQProvider
    provider = IonQProvider(os.getenv("IONQ_API_KEY"))
    return provider.get_backend("ionq_simulator", gateset="native")


def _run(qc, shots=200):
    from qiskit import transpile
    backend = _ionq_simulator_backend()
    t_qc = transpile(qc, backend=backend, optimization_level=1)
    job = backend.run(t_qc, shots=shots)
    return job.result().get_counts()


# --------------------------------------------------------------- endianness

def test_asymmetric_single_flip_endianness():
    """X on qubit 0 ONLY, n=3 qubits — deliberately asymmetric.

    A Bell state or GHZ chain is symmetric under bit-reversal and would give
    the SAME (wrong-looking-right) answer whether or not there's an
    endianness bug. This circuit isn't: Qiskit's convention says classical
    bit 0 is the rightmost character, so X on qubit 0 alone must read '001'.
    If this ever reads '100' instead, IonQ's result parsing has picked up a
    bit-reversal somewhere in the pipeline — silent, and invisible to any
    symmetric test.
    """
    from qiskit import QuantumCircuit

    qc = QuantumCircuit(3, 3)
    qc.x(0)
    qc.measure([0, 1, 2], [0, 1, 2])

    counts = _run(qc)
    assert set(counts.keys()) == {"001"}, (
        f"Endianness mismatch: expected only '001' (qubit 0 = rightmost bit), got {counts}"
    )


def test_asymmetric_two_qubit_pattern():
    """X on qubits 0 and 2 only (not 1), n=4 — a second, differently-shaped
    asymmetric pattern, in case a single-bit flip alone could coincidentally
    survive a more exotic reordering bug (e.g. a rotation rather than a
    reversal)."""
    from qiskit import QuantumCircuit

    qc = QuantumCircuit(4, 4)
    qc.x(0)
    qc.x(2)
    qc.measure([0, 1, 2, 3], [0, 1, 2, 3])

    counts = _run(qc)
    assert set(counts.keys()) == {"0101"}, (
        f"Endianness mismatch: expected only '0101' (bits 0 and 2 set, rightmost-indexed), got {counts}"
    )


# ------------------------------------------------------------ angle units

def test_rx_pi_is_a_full_flip():
    """RX(pi) must deterministically flip |0> -> |1>.

    IonQ's native gates are parameterized in TURNS (1 turn = 2*pi radians),
    not radians. Qiskit circuits are built in radians. If that conversion
    silently drops a factor of 2*pi anywhere between our QASM and IonQ's
    native gate call, RX(pi) stops being a clean flip and becomes some other,
    wrong rotation — the failure is a probability distribution that LOOKS
    plausible (not an error), which is exactly the dangerous kind.
    """
    from qiskit import QuantumCircuit

    qc = QuantumCircuit(1, 1)
    qc.rx(math.pi, 0)
    qc.measure(0, 0)

    counts = _run(qc)
    total = sum(counts.values())
    ones_fraction = counts.get("1", 0) / total
    assert ones_fraction > 0.98, (
        f"RX(pi) should deterministically flip to |1> (angle-unit bug suspected): got {counts}"
    )


def test_rx_half_pi_is_a_50_50_split():
    """RX(pi/2) on |0> must give ~50/50 — a different angle magnitude than
    the full-flip test above, so a bug that only breaks at pi (a boundary
    case) rather than at an arbitrary angle wouldn't hide behind this pair."""
    from qiskit import QuantumCircuit

    qc = QuantumCircuit(1, 1)
    qc.rx(math.pi / 2, 0)
    qc.measure(0, 0)

    counts = _run(qc, shots=1000)
    total = sum(counts.values())
    ones_fraction = counts.get("1", 0) / total
    assert 0.35 < ones_fraction < 0.65, (
        f"RX(pi/2) should give ~50/50 split (angle-unit bug suspected): got {counts}"
    )
