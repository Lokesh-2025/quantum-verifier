"""
Injected-bug benchmark for the Verifier and control-experiment generator.

Confirms the Verifier BLOCKS the known failure classes this project has
actually hit (malformed circuits, IBM heavy-hex routing violations, false
claims that don't survive contact with a real noise model), and PASSES
genuinely good circuits with no false positives — including the real E1
circuits from the private ionq-singmasters repo, whose predictions were
independently verified earlier this project.

No hardware required — everything here runs on the free IonQ simulator
and IBM's local Aer simulator. Skips automatically if IONQ_API_KEY isn't
configured.
"""
import os
import pytest
from dotenv import load_dotenv

load_dotenv()

IONQ_KEY_PRESENT = bool(os.getenv("IONQ_API_KEY"))
pytestmark = pytest.mark.skipif(
    not IONQ_KEY_PRESENT, reason="IONQ_API_KEY not set — skipping live IonQ verifier tests"
)

import core.verifier as v
import core.control_experiment as ce

BELL = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0],q[1];
measure q[0] -> c[0];
measure q[1] -> c[1];
"""

E1_SINGLE = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[7];
creg c[7];
h q[0];
h q[1];
h q[2];
h q[3];
h q[4];
h q[5];
h q[6];
rz(4.108195299777758) q[1];
rz(4.108195299777758) q[2];
rz(4.108195299777758) q[3];
rz(-4.108195299777758) q[4];
rz(-4.108195299777758) q[5];
rzz(2.054097649888879) q[0],q[6];
rx(2.095375424636625) q[0];
rx(2.095375424636625) q[1];
rx(2.095375424636625) q[2];
rx(2.095375424636625) q[3];
rx(2.095375424636625) q[4];
rx(2.095375424636625) q[5];
rx(2.095375424636625) q[6];
rz(4.108195299777758) q[1];
rz(4.108195299777758) q[2];
rz(4.108195299777758) q[3];
rz(-4.108195299777758) q[4];
rz(-4.108195299777758) q[5];
rzz(2.054097649888879) q[0],q[6];
rx(2.095375424636625) q[0];
rx(2.095375424636625) q[1];
rx(2.095375424636625) q[2];
rx(2.095375424636625) q[3];
rx(2.095375424636625) q[4];
rx(2.095375424636625) q[5];
rx(2.095375424636625) q[6];
rz(4.108195299777758) q[1];
rz(4.108195299777758) q[2];
rz(4.108195299777758) q[3];
rz(-4.108195299777758) q[4];
rz(-4.108195299777758) q[5];
rzz(2.054097649888879) q[0],q[6];
rx(2.095375424636625) q[0];
rx(2.095375424636625) q[1];
rx(2.095375424636625) q[2];
rx(2.095375424636625) q[3];
rx(2.095375424636625) q[4];
rx(2.095375424636625) q[5];
rx(2.095375424636625) q[6];
measure q[0] -> c[0];
measure q[1] -> c[1];
measure q[2] -> c[2];
measure q[3] -> c[3];
measure q[4] -> c[4];
measure q[5] -> c[5];
measure q[6] -> c[6];
"""

E1_RING = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[7];
creg c[7];
h q[0];
h q[1];
h q[2];
h q[3];
h q[4];
h q[5];
h q[6];
rz(5.6006883049590375) q[1];
rz(5.6006883049590375) q[2];
rz(5.6006883049590375) q[3];
rz(-5.6006883049590375) q[4];
rz(-5.6006883049590375) q[5];
rzz(2.8003441524795187) q[0],q[6];
rzz(-5.6006883049590375) q[1],q[2];
rzz(-5.6006883049590375) q[2],q[3];
rzz(5.6006883049590375) q[3],q[4];
rzz(-5.6006883049590375) q[4],q[5];
rx(1.2205867671120292) q[0];
rx(1.2205867671120292) q[1];
rx(1.2205867671120292) q[2];
rx(1.2205867671120292) q[3];
rx(1.2205867671120292) q[4];
rx(1.2205867671120292) q[5];
rx(1.2205867671120292) q[6];
rz(5.6006883049590375) q[1];
rz(5.6006883049590375) q[2];
rz(5.6006883049590375) q[3];
rz(-5.6006883049590375) q[4];
rz(-5.6006883049590375) q[5];
rzz(2.8003441524795187) q[0],q[6];
rzz(-5.6006883049590375) q[1],q[2];
rzz(-5.6006883049590375) q[2],q[3];
rzz(5.6006883049590375) q[3],q[4];
rzz(-5.6006883049590375) q[4],q[5];
rx(1.2205867671120292) q[0];
rx(1.2205867671120292) q[1];
rx(1.2205867671120292) q[2];
rx(1.2205867671120292) q[3];
rx(1.2205867671120292) q[4];
rx(1.2205867671120292) q[5];
rx(1.2205867671120292) q[6];
measure q[0] -> c[0];
measure q[1] -> c[1];
measure q[2] -> c[2];
measure q[3] -> c[3];
measure q[4] -> c[4];
measure q[5] -> c[5];
measure q[6] -> c[6];
"""

TARGET_BITSTRINGS = ["0001111", "1001110"]  # rows 15, 78 (subset of E1's 3-row target set)


# ------------------------------------------------------------- semantic checks

def test_blocks_empty_circuit():
    empty = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\n'
    result = v.verify(empty, provider="ionq", target_device="simulator", shots=100)
    assert result["verdict"] == "BLOCK"
    assert any(i["check"] == "empty_circuit" for i in result["semantic_check"]["issues"])


def test_blocks_circuit_without_measurements():
    no_measure = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\nh q[0];\ncx q[0],q[1];\n'
    result = v.verify(no_measure, provider="ionq", target_device="simulator", shots=100)
    assert result["verdict"] == "BLOCK"
    assert any(i["check"] == "no_measurements" for i in result["semantic_check"]["issues"])


# ------------------------------------------------------------- topology checks (IBM only)

def test_blocks_ibm_degree4_topology_violation():
    """A qubit interacting with 4 others exceeds heavy-hex's degree-3 limit —
    the exact failure class (263->1037 gates) this project hit and documented."""
    degree4 = (
        'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[5];\ncreg c[5];\n'
        "cx q[0],q[1];\ncx q[0],q[2];\ncx q[0],q[3];\ncx q[0],q[4];\n"
        "measure q[0] -> c[0];\nmeasure q[1] -> c[1];\nmeasure q[2] -> c[2];\n"
        "measure q[3] -> c[3];\nmeasure q[4] -> c[4];\n"
    )
    from core.verifier import _parse, topology_check
    circuit = _parse(degree4)
    result = topology_check(circuit, provider="ibm")
    assert result["passed"] is False
    assert result["violations"][0]["qubit"] == 0
    assert result["violations"][0]["degree"] == 4


def test_ibm_topology_check_passes_clean_circuit():
    from core.verifier import _parse, topology_check
    circuit = _parse(BELL)
    result = topology_check(circuit, provider="ibm")
    assert result["passed"] is True
    assert result["violations"] == []


def test_ionq_has_no_topology_risk():
    """IonQ is all-to-all -- the SAME degree-4 circuit that blocks on IBM must
    be explicitly marked not-applicable (not silently skipped) on IonQ."""
    degree4 = (
        'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[5];\ncreg c[5];\n'
        "cx q[0],q[1];\ncx q[0],q[2];\ncx q[0],q[3];\ncx q[0],q[4];\n"
        "measure q[0] -> c[0];\nmeasure q[1] -> c[1];\nmeasure q[2] -> c[2];\n"
        "measure q[3] -> c[3];\nmeasure q[4] -> c[4];\n"
    )
    from core.verifier import _parse, topology_check
    circuit = _parse(degree4)
    result = topology_check(circuit, provider="ionq")
    assert result["applicable"] is False
    assert result["passed"] is True


# ------------------------------------------------------------- ground-truth checks

def test_blocks_false_amplification_claim():
    result = v.verify(BELL, provider="ionq", target_device="simulator", shots=512,
                       expected_marked_bitstrings=["00"], expected_amplification=10.0)
    assert result["verdict"] == "BLOCK"
    assert "not distinguishable" in result["reason"].lower()


def test_passes_true_amplification_claim():
    """Bell state genuinely gives ~50% on '00' and '11' each -- against a
    baseline of 25% for one 2-qubit bitstring, that's really ~2x amplification."""
    result = v.verify(BELL, provider="ionq", target_device="simulator", shots=1024,
                       expected_marked_bitstrings=["00", "11"], expected_amplification=2.0)
    assert result["verdict"] == "GO"


# ------------------------------------------------------------- real E1 circuits (known-good)

@pytest.mark.parametrize("qasm,expected_amp", [
    (E1_SINGLE, 42.1),
    (E1_RING, 21.5),
])
def test_real_e1_circuit_passes_with_its_verified_prediction(qasm, expected_amp):
    """These predictions were independently verified earlier this project
    (simulator self-check matched within tolerance before any real hardware
    run) -- the Verifier must agree, not just accept anything."""
    result = v.verify(qasm, provider="ionq", target_device="simulator", shots=1024,
                       expected_marked_bitstrings=TARGET_BITSTRINGS,
                       expected_amplification=expected_amp, amplification_tolerance=0.6)
    assert result["verdict"] == "GO", result.get("reason")


# ------------------------------------------------------------- control experiment

def test_control_experiment_isolates_real_entangling_effect():
    result = ce.falsify(E1_RING, provider="ionq", target_device="simulator", shots=1024,
                         marked_bitstrings=TARGET_BITSTRINGS)
    assert result["entangling_gates_removed"] == 10
    assert "isolated_effect_size" in result
    assert isinstance(result["isolated_effect_size"], float)


def test_control_experiment_refuses_circuit_with_no_entanglement():
    single_qubit_only = (
        'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[1];\ncreg c[1];\n'
        "h q[0];\nmeasure q[0] -> c[0];\n"
    )
    result = ce.falsify(single_qubit_only, provider="ionq", target_device="simulator", shots=256)
    assert "error" in result
    assert "No entangling gates" in result["error"]
