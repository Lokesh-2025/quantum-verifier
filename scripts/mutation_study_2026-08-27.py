"""
Standalone mutation-testing measurement script. NOT wired into verify(),
NOT exposed as an MCP tool. Run manually, once, to produce a report judged
against docs/pre-registration-mutation-study-2026-08-27.md (already locked,
already pushed as commit 42e1e16 -- criteria are NOT re-derived here).

Corpus: the academic "Quantum Circuit Mutants" corpus (723k mutants, 382
circuits) is NOT available in this environment or as a downloadable dataset
(checked: no local copy, no dataset link in the paper or its GitHub repo;
only the mutation-GENERATION tool "muskit" is pip-installable, which is not
the same as the corpus). Per the fallback instruction, mutants are built
from this project's own known-answer GHZ template (core/templates.py),
using Clifford-only add/remove/replace operators so ground truth is exact
(via core/stabilizer.py's stabilizer-tableau computation, not simulated) --
lets equivalent/no-op mutants be identified and excluded rigorously rather
than guessed at.

Checks under test: OLD = core/verifier.py's ground_truth_check (tolerance
band). NEW = core/verifier.py's ground_truth_significance_test (TOST
equivalence test). Nothing else.

Simulator only: qiskit_aer.AerSimulator, purely local, zero network calls,
zero cost -- NOT IonQ's cloud simulator (which requires a real API key and
submits real jobs over the network even on its free tier; running
thousands of those would be a real-account-touching action this script
must not take).
"""
import itertools
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator

from core.templates import ghz_parity_check_circuit
from core.stabilizer import verify_stabilizer_circuit
from core.verifier import ground_truth_check, ground_truth_significance_test

RNG = random.Random(20260827)  # seeded for reproducibility

CLIFFORD_1Q = ["x", "y", "z", "s"]
CLIFFORD_2Q_REPLACEMENTS = {"cx": ["cz"]}


# --------------------------------------------------------------- mutation generation

def _instr_list(circuit):
    """[(name, qubit_indices), ...] for every real (non-measure) instruction."""
    out = []
    for instr in circuit.data:
        name = instr.operation.name
        if name in ("measure", "barrier"):
            continue
        qubits = [circuit.find_bit(q).index for q in instr.qubits]
        out.append((name, qubits))
    return out


def _rebuild(n_qubits, ops):
    """ops: list of (name, qubit_indices) -> a fresh QuantumCircuit with measurements."""
    qc = QuantumCircuit(n_qubits, n_qubits)
    for name, qubits in ops:
        getattr(qc, name)(*qubits)
    qc.measure(range(n_qubits), range(n_qubits))
    return qc


# Class assignment below is based on MEASURED exact effect size (via the
# stabilizer tableau), not an a priori guess about which gate edit "sounds"
# bigger or smaller -- an earlier version of this script grouped by naive
# operator intuition (remove/replace-h -> "obvious", replace-cx/add-gate ->
# "subtle") and the real, measured numbers came back backwards: the
# intended-"subtle" class had a LARGER mean effect (0.96) than the
# intended-"obvious" class (0.50). Re-measured and reassigned before running
# the actual study, not after seeing detection results -- the batch is
# rebuilt and detection is computed fresh under this corrected assignment.
#
# obvious (add_gate): inserting an extra Clifford gate at a random position
# consistently drives the exact marked probability toward 0 (a clean,
# unambiguous "this circuit no longer produces the claimed GHZ signature").
# subtle (remove_cx / replace_cx_with_cz): both land the exact marked
# probability at EXACTLY 0.5 for this circuit family, regardless of which
# CX is targeted or how many qubits -- a real, structural property of GHZ's
# binary branching (see the report), not a bug in this script. 0.5 sits
# exactly at the historical amplification_tolerance=0.5 default's boundary,
# making it a genuinely hard, boundary-level detection case -- legitimately
# "subtle" in the sense of hard-to-detect, even though it isn't a smaller
# circuit edit than the obvious class.

def mutate_obvious(base_ops, n_qubits, rng):
    """Add one extra single-qubit Clifford gate at a random position --
    measured to drive the exact marked probability toward 0 (unambiguous)."""
    ops = list(base_ops)
    pos = rng.randint(0, len(ops))
    qubit = rng.randint(0, n_qubits - 1)
    gate = rng.choice(CLIFFORD_1Q)
    ops.insert(pos, (gate, [qubit]))
    return ops, f"add_{gate}@{pos}(q{qubit})"


def mutate_subtle(base_ops, n_qubits, rng):
    """Remove one entangling gate, or replace one cx with cz -- both
    measured to land the exact marked probability at exactly 0.5, right at
    the historical tolerance boundary: a genuinely hard detection case."""
    ops = list(base_ops)
    choice = rng.choice(["remove_cx", "replace_cx_with_cz"])
    cx_positions = [i for i, (name, _) in enumerate(ops) if name == "cx"]
    if not cx_positions:
        return ops, "no_cx_available"
    pos = rng.choice(cx_positions)
    if choice == "remove_cx":
        del ops[pos]
        return ops, f"remove_cx@{pos}"
    _, qubits = ops[pos]
    ops[pos] = ("cz", qubits)
    return ops, f"replace_cx_with_cz@{pos}"


def exact_marked_probability(circuit, marked_bitstrings):
    """Exact (not simulated) probability of landing in the marked set, via
    the stabilizer tableau -- the real ground truth for a Clifford circuit."""
    pred = verify_stabilizer_circuit(circuit)
    if not pred["applicable"]:
        return None
    probs = pred["exact_probabilities"]
    return sum(probs.get(b, 0.0) for b in marked_bitstrings)


# --------------------------------------------------------------- study

def build_batch(n_qubits_list, n_per_class_per_n, rng):
    """Returns dict: n_qubits -> {"obvious": [...], "subtle": [...]} of
    (mutant_circuit, mutation_label, exact_marked_prob) tuples, EXCLUDING
    equivalent mutants (exact_marked_prob == original's, i.e. no real
    behavioral change -- nothing for either check to detect)."""
    batch = {}
    for n in n_qubits_list:
        base = ghz_parity_check_circuit(n)
        base_ops = _instr_list(base)
        marked = ["0" * n, "1" * n]
        original_p = exact_marked_probability(base, marked)
        assert original_p is not None and abs(original_p - 1.0) < 1e-9, \
            f"GHZ base circuit at n={n} should have exact marked probability 1.0, got {original_p}"

        classes = {"obvious": [], "subtle": []}
        for cls, mutate_fn in [("obvious", mutate_obvious), ("subtle", mutate_subtle)]:
            seen_labels = set()
            attempts = 0
            while len(classes[cls]) < n_per_class_per_n and attempts < n_per_class_per_n * 20:
                attempts += 1
                ops, label = mutate_fn(base_ops, n, rng)
                key = (n, cls, label)
                mutant = _rebuild(n, ops)
                p = exact_marked_probability(mutant, marked)
                if p is None:
                    continue  # mutation left the Clifford group (shouldn't happen here) -- skip
                if abs(p - original_p) < 1e-9:
                    continue  # equivalent mutant -- no real behavioral change, exclude
                if key in seen_labels and label.split("@")[0] not in ("add_x", "add_y", "add_z", "add_s"):
                    continue  # avoid exact duplicate non-randomized mutations
                seen_labels.add(key)
                classes[cls].append((mutant, label, p, original_p))
        batch[n] = classes
    return batch


def run_checks_on(circuit, marked_bitstrings, expected_amplification, shots, tolerance=0.5, sim_seed=None):
    sim = AerSimulator(seed_simulator=sim_seed)
    counts = sim.run(circuit, shots=shots, seed_simulator=sim_seed).result().get_counts()
    old = ground_truth_check(counts, marked_bitstrings, expected_amplification, tolerance)
    new = ground_truth_significance_test(counts, marked_bitstrings, expected_amplification, tolerance)
    return old, new


def detected_old(old_result):
    """OLD check's own definition of 'flagged as not matching the claim'."""
    return old_result.get("applicable") and not old_result["within_tolerance"]


def detected_new(new_result):
    """NEW check's own definition: only a confident FAIL counts as
    'detected'. INCONCLUSIVE is deliberately NOT counted as detection --
    it means 'not enough shots to tell', not 'caught the bug', matching
    Task 0b's finding that these two states must never be conflated."""
    return new_result.get("applicable") and new_result["tost_verdict"] == "FAIL"


def main():
    n_qubits_list = [3, 4, 5]
    n_per_class_per_n = 40  # -> up to 3*40*2 = 240 distinct mutants x 2 shot counts x (mutants+controls)
    shot_counts = [1024, 8192]
    tolerance = 0.5  # matches ground_truth_check's own default, used for both checks identically

    print(f"Building mutant batch (seed=20260827, classes=obvious/subtle, n_qubits={n_qubits_list})...")
    batch = build_batch(n_qubits_list, n_per_class_per_n, RNG)

    total_mutants = sum(len(batch[n][c]) for n in n_qubits_list for c in ("obvious", "subtle"))
    print(f"Generated {total_mutants} non-equivalent mutants "
          f"({total_mutants * len(shot_counts)} total mutant simulation runs).")

    # sanity: report mean effect size per class (diagnostic, not a criterion)
    for cls in ("obvious", "subtle"):
        drops = [abs(p - orig) for n in n_qubits_list for (_, _, p, orig) in batch[n][cls]]
        print(f"  {cls}: n={len(drops)}, mean |Δexact marked prob| = {sum(drops)/len(drops):.4f}, "
              f"min={min(drops):.4f}, max={max(drops):.4f}")

    results = {}  # (cls, shots) -> {"old_detected": int, "new_detected": int, "total": int}
    verdict_breakdown = {}  # (cls, shots) -> {"VERIFIED": int, "FAIL": int, "INCONCLUSIVE": int}
    seed_counter = itertools.count(1)
    for n in n_qubits_list:
        marked = ["0" * n, "1" * n]
        amp = 2 ** (n - 1)  # expected_amplification s.t. baseline(2/2^n) * amp == 1.0
        for cls in ("obvious", "subtle"):
            for mutant, label, p, orig in batch[n][cls]:
                for shots in shot_counts:
                    old, new = run_checks_on(mutant, marked, amp, shots, tolerance,
                                              sim_seed=next(seed_counter))
                    key = (cls, shots)
                    r = results.setdefault(key, {"old_detected": 0, "new_detected": 0, "total": 0})
                    r["total"] += 1
                    r["old_detected"] += int(detected_old(old))
                    r["new_detected"] += int(detected_new(new))
                    vb = verdict_breakdown.setdefault(key, {"VERIFIED": 0, "FAIL": 0, "INCONCLUSIVE": 0})
                    if new.get("applicable"):
                        vb[new["tost_verdict"]] += 1

    # false-positive rate: run the UNMUTATED circuits through both checks too
    fp_results = {}  # shots -> {"old_fp": int, "new_fp": int, "total": int}
    n_control_runs_per_n = 30
    for n in n_qubits_list:
        base = ghz_parity_check_circuit(n)
        marked = ["0" * n, "1" * n]
        amp = 2 ** (n - 1)
        for shots in shot_counts:
            for _ in range(n_control_runs_per_n):
                old, new = run_checks_on(base, marked, amp, shots, tolerance,
                                          sim_seed=next(seed_counter))
                r = fp_results.setdefault(shots, {"old_fp": 0, "new_fp": 0, "total": 0})
                r["total"] += 1
                r["old_fp"] += int(detected_old(old))  # a "detection" on an unmutated circuit = false positive
                r["new_fp"] += int(detected_new(new))

    print()
    print("=== DETECTION RATES (mutant classes) ===")
    for cls in ("obvious", "subtle"):
        for shots in shot_counts:
            r = results[(cls, shots)]
            print(f"{cls:8s} shots={shots:5d}  n={r['total']:4d}  "
                  f"OLD detected={r['old_detected']:4d} ({100*r['old_detected']/r['total']:.1f}%)  "
                  f"NEW detected={r['new_detected']:4d} ({100*r['new_detected']/r['total']:.1f}%)")

    print()
    print("=== NEW check's tost_verdict breakdown, per class/shots (VERIFIED = confidently wrong "
          "on a mutant; FAIL = detected; INCONCLUSIVE = honestly 'not enough shots') ===")
    for cls in ("obvious", "subtle"):
        for shots in shot_counts:
            vb = verdict_breakdown[(cls, shots)]
            total = sum(vb.values())
            print(f"{cls:8s} shots={shots:5d}  VERIFIED={vb['VERIFIED']:4d} "
                  f"({100*vb['VERIFIED']/total:.1f}%)  FAIL={vb['FAIL']:4d} ({100*vb['FAIL']/total:.1f}%)  "
                  f"INCONCLUSIVE={vb['INCONCLUSIVE']:4d} ({100*vb['INCONCLUSIVE']/total:.1f}%)")

    print()
    print("=== FALSE-POSITIVE RATES (unmutated controls) ===")
    for shots in shot_counts:
        r = fp_results[shots]
        print(f"shots={shots:5d}  n={r['total']:4d}  "
              f"OLD false-positives={r['old_fp']:3d} ({100*r['old_fp']/r['total']:.2f}%)  "
              f"NEW false-positives={r['new_fp']:3d} ({100*r['new_fp']/r['total']:.2f}%)")

    output = {"results": {f"{cls}|{shots}": v for (cls, shots), v in results.items()},
              "verdict_breakdown": {f"{cls}|{shots}": v for (cls, shots), v in verdict_breakdown.items()},
              "fp_results": {str(shots): v for shots, v in fp_results.items()},
              "n_qubits_list": n_qubits_list, "shot_counts": shot_counts,
              "n_per_class_per_n": n_per_class_per_n, "tolerance": tolerance}
    with open("/tmp/mutation_study_raw_results.json", "w") as f:
        json.dump(output, f, indent=2)
    print("\nRaw results written to /tmp/mutation_study_raw_results.json")


if __name__ == "__main__":
    main()
