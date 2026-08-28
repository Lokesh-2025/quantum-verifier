"""
Second mutation-testing batch. Standalone measurement script. NOT wired into
verify(), NOT exposed as an MCP tool. Judged against the SAME locked
docs/pre-registration-mutation-study-2026-08-27.md (commit 42e1e16) --
criteria not re-derived, equivalence-test parameters not tuned. This is an
honest second measurement, not a rematch.

Why this batch exists: batch 1 (scripts/mutation_study_2026-08-27.py,
docs/mutation-study-report-2026-08-27.md) tested GHZ circuits, and its
"subtle" mutation class happened to land the true probability at EXACTLY
0.5 -- the old check's own default tolerance boundary. That's an unusually
hard, edge-of-the-line case for any statistical test, so it wasn't clear if
batch 1's negative result was really about the new test being weaker, or
just about that one circuit family happening to sit exactly on a decision
boundary. This batch uses a structurally different circuit to check.

Circuit: a CX-based RING (cycle), not GHZ's STAR. H on qubit 0, then
CX(0,1), CX(1,2), ..., CX(n-2,n-1), CX(n-1,0) closing the loop back to
qubit 0. Confirmed directly before building this script: a CZ-based "true"
graph state (H on every qubit, then CZ around a ring -- the original ask)
is uniform over ALL 2^n outcomes for n=3,4,5, which would make "marked
bitstrings" mean "everything" -- a meaningless comparison, since nothing
could ever violate that claim. CZ only imparts phase, never correlates the
actual measured VALUES between qubits the way CX does, which is why GHZ
(built from CX) has a small, meaningful marked set and a CZ-graph-state
doesn't. The CX-ring keeps CX's real value-correlation property (so there's
still a small, meaningful marked-bitstring signature to test against) while
being a genuinely different TOPOLOGY from GHZ's star (confirmed: n=3 gives
support={'000': 0.5, '110': 0.5}, not GHZ's {'000': 0.5, '111': 0.5}).

Same discipline as batch 1: exact (stabilizer-tableau) ground truth, not
simulated; obvious/subtle classified by MEASURED effect size, not guessed;
honest sample size, no padding; same shot counts (1024, 8192), simulator
only, zero cost; VERIFIED/FAIL/INCONCLUSIVE breakdown, not just raw
detection rate; both mutant selection AND simulator shots seeded (batch 1's
real reproducibility bug, not reintroduced here).
"""
import itertools
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator

from core.stabilizer import verify_stabilizer_circuit
from core.verifier import ground_truth_check, ground_truth_significance_test

RNG = random.Random(2026082702)  # seeded for reproducibility, distinct from batch 1's seed

CLIFFORD_1Q = ["x", "y", "z", "s"]


# --------------------------------------------------------------- circuit

def cx_ring_circuit(n_qubits):
    """H on qubit 0, then a CX chain that wraps around to close a cycle --
    structurally a ring, not GHZ's star (one central qubit connected to
    everyone)."""
    if n_qubits < 3:
        raise ValueError("CX ring needs at least 3 qubits to be a real cycle, not just a 2-qubit pair")
    qc = QuantumCircuit(n_qubits, n_qubits)
    qc.h(0)
    for i in range(n_qubits - 1):
        qc.cx(i, i + 1)
    qc.cx(n_qubits - 1, 0)  # close the ring
    qc.measure(range(n_qubits), range(n_qubits))
    return qc


# --------------------------------------------------------------- mutation generation

def _instr_list(circuit):
    out = []
    for instr in circuit.data:
        name = instr.operation.name
        if name in ("measure", "barrier"):
            continue
        qubits = [circuit.find_bit(q).index for q in instr.qubits]
        out.append((name, qubits))
    return out


def _rebuild(n_qubits, ops):
    qc = QuantumCircuit(n_qubits, n_qubits)
    for name, qubits in ops:
        getattr(qc, name)(*qubits)
    qc.measure(range(n_qubits), range(n_qubits))
    return qc


def mutate_add_gate(base_ops, n_qubits, rng):
    """ADD: insert one extra single-qubit Clifford gate at a random position."""
    ops = list(base_ops)
    pos = rng.randint(0, len(ops))
    qubit = rng.randint(0, n_qubits - 1)
    gate = rng.choice(CLIFFORD_1Q)
    ops.insert(pos, (gate, [qubit]))
    return ops, f"add_{gate}@{pos}(q{qubit})"


def mutate_weaken_entangler(base_ops, n_qubits, rng):
    """REMOVE or REPLACE: remove one CX from the ring, or replace one CX
    with CZ. Same operators as batch 1 -- effect size is MEASURED fresh
    below, not assumed to match GHZ's."""
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
    pred = verify_stabilizer_circuit(circuit)
    if not pred["applicable"]:
        return None
    probs = pred["exact_probabilities"]
    return sum(probs.get(b, 0.0) for b in marked_bitstrings)


# --------------------------------------------------------------- study

def build_raw_pool(n_qubits_list, n_per_operator_per_n, rng):
    """Generate mutants from BOTH operators, tagged with their real
    operator name and MEASURED effect size -- classification into
    obvious/subtle happens after measuring, in main(), not here."""
    pool = {}  # n -> {"add_gate": [...], "weaken_entangler": [...]}
    for n in n_qubits_list:
        base = cx_ring_circuit(n)
        base_ops = _instr_list(base)
        marked = sorted(verify_stabilizer_circuit(base)["exact_probabilities"].keys())
        original_p = exact_marked_probability(base, marked)
        assert original_p is not None and abs(original_p - 1.0) < 1e-9, \
            f"CX-ring base circuit at n={n} should have exact marked probability 1.0 " \
            f"(marked = its own real support by construction), got {original_p}"

        operators = {"add_gate": mutate_add_gate, "weaken_entangler": mutate_weaken_entangler}
        pool[n] = {"marked": marked, "operators": {}}
        for op_name, mutate_fn in operators.items():
            seen_labels = set()
            attempts = 0
            found = []
            while len(found) < n_per_operator_per_n and attempts < n_per_operator_per_n * 20:
                attempts += 1
                ops, label = mutate_fn(base_ops, n, rng)
                mutant = _rebuild(n, ops)
                p = exact_marked_probability(mutant, marked)
                if p is None:
                    continue
                if abs(p - original_p) < 1e-9:
                    continue  # equivalent mutant -- exclude
                key = (n, op_name, label)
                if key in seen_labels and label.split("@")[0] not in ("add_x", "add_y", "add_z", "add_s"):
                    continue
                seen_labels.add(key)
                found.append((mutant, label, p, original_p))
            pool[n]["operators"][op_name] = found
    return pool


def run_checks_on(circuit, marked_bitstrings, expected_amplification, shots, tolerance=0.5, sim_seed=None):
    sim = AerSimulator(seed_simulator=sim_seed)
    counts = sim.run(circuit, shots=shots, seed_simulator=sim_seed).result().get_counts()
    old = ground_truth_check(counts, marked_bitstrings, expected_amplification, tolerance)
    new = ground_truth_significance_test(counts, marked_bitstrings, expected_amplification, tolerance)
    return old, new


def detected_old(old_result):
    return old_result.get("applicable") and not old_result["within_tolerance"]


def detected_new(new_result):
    return new_result.get("applicable") and new_result["tost_verdict"] == "FAIL"


def main():
    n_qubits_list = [3, 4, 5]
    n_per_operator_per_n = 40
    shot_counts = [1024, 8192]
    tolerance = 0.5

    print(f"Building raw mutant pool (seed=2026082702, operators=add_gate/weaken_entangler, "
          f"n_qubits={n_qubits_list})...")
    pool = build_raw_pool(n_qubits_list, n_per_operator_per_n, RNG)

    print()
    print("=== MEASURED effect size per operator (diagnostic -- decides classification) ===")
    for op_name in ("add_gate", "weaken_entangler"):
        drops = [abs(p - orig) for n in n_qubits_list for (_, _, p, orig) in pool[n]["operators"][op_name]]
        n_total = sum(len(pool[n]["operators"][op_name]) for n in n_qubits_list)
        print(f"  {op_name}: n={n_total}, mean |Δexact marked prob| = {sum(drops)/len(drops):.4f}, "
              f"min={min(drops):.4f}, max={max(drops):.4f}")

    # Classify by measured effect size: whichever operator has the LARGER
    # mean effect is "obvious", the smaller is "subtle" -- decided from the
    # numbers just printed, not assumed to mirror batch 1's assignment.
    means = {}
    for op_name in ("add_gate", "weaken_entangler"):
        drops = [abs(p - orig) for n in n_qubits_list for (_, _, p, orig) in pool[n]["operators"][op_name]]
        means[op_name] = sum(drops) / len(drops)
    obvious_op = max(means, key=means.get)
    subtle_op = min(means, key=means.get)
    print(f"\nClassification (measured): obvious={obvious_op} (mean={means[obvious_op]:.4f}), "
          f"subtle={subtle_op} (mean={means[subtle_op]:.4f})")
    if obvious_op == subtle_op:
        print("WARNING: both operators have identical mean effect size -- classification is degenerate.")

    batch = {}
    for n in n_qubits_list:
        batch[n] = {"obvious": pool[n]["operators"][obvious_op],
                     "subtle": pool[n]["operators"][subtle_op],
                     "marked": pool[n]["marked"]}

    total_mutants = sum(len(batch[n][c]) for n in n_qubits_list for c in ("obvious", "subtle"))
    print(f"\nTotal non-equivalent mutants: {total_mutants} "
          f"({total_mutants * len(shot_counts)} total mutant simulation runs).")

    results = {}
    verdict_breakdown = {}
    seed_counter = itertools.count(1)
    for n in n_qubits_list:
        marked = batch[n]["marked"]
        baseline_p = len(marked) / (2 ** n)
        amp = 1.0 / baseline_p  # s.t. baseline_p * amp == 1.0, matching batch 1's convention
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

    fp_results = {}
    n_control_runs_per_n = 30
    for n in n_qubits_list:
        base = cx_ring_circuit(n)
        marked = batch[n]["marked"]
        baseline_p = len(marked) / (2 ** n)
        amp = 1.0 / baseline_p
        for shots in shot_counts:
            for _ in range(n_control_runs_per_n):
                old, new = run_checks_on(base, marked, amp, shots, tolerance,
                                          sim_seed=next(seed_counter))
                r = fp_results.setdefault(shots, {"old_fp": 0, "new_fp": 0, "total": 0})
                r["total"] += 1
                r["old_fp"] += int(detected_old(old))
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
    print("=== NEW check's tost_verdict breakdown, per class/shots ===")
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
              "obvious_op": obvious_op, "subtle_op": subtle_op, "means": means,
              "n_qubits_list": n_qubits_list, "shot_counts": shot_counts,
              "n_per_operator_per_n": n_per_operator_per_n, "tolerance": tolerance}
    with open("/tmp/mutation_study_batch2_raw_results.json", "w") as f:
        json.dump(output, f, indent=2)
    print("\nRaw results written to /tmp/mutation_study_batch2_raw_results.json")


if __name__ == "__main__":
    main()
