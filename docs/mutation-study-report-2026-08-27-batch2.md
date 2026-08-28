# Second mutation-testing batch: does batch 1's pattern hold on a different topology?

Date: 2026-08-27 (same day as batch 1). Judged against the SAME locked `docs/pre-registration-mutation-study-2026-08-27.md` (commit `42e1e16`) — criteria not re-derived, equivalence-test parameters not tuned. This is an honest second measurement, not a rematch.

**The question this batch exists to answer:** batch 1 tested GHZ circuits, and its "subtle" mutation class happened to land the true probability at exactly 0.5 — the old check's own default tolerance boundary. Was batch 1's negative result really about the new test being weaker, or an artifact of testing at one specific, unusually hard boundary case?

**Short answer, up front: the pattern reproduced — but not as a coincidence. It's mechanistically explained below, on a genuinely different circuit topology, which makes it a stronger finding, not a weaker one.**

Harness: `scripts/mutation_study_2026-08-27_batch2.py`. Fully reproducible — verified by running twice and diffing identical output.

---

## The circuit swap, and a real correction made before building anything

The original ask was a true **graph state**: H on every qubit, then CZ around a ring. Checked directly before writing any harness code — for n=3, 4, 5, this is **perfectly uniform across all 2ⁿ possible outcomes**. CZ only imparts phase, it never correlates the actual measured *values* between qubits the way CX does — so a CZ-based graph state has no small, meaningful marked-bitstring signature at all. Using it as specified would have made "marked bitstrings" mean "everything," a meaningless comparison.

**Substitute used instead:** a **CX-based ring** — H on qubit 0, then a CX chain that wraps around to close a cycle back to qubit 0, instead of GHZ's **star** (one central qubit connected to everyone). This keeps CX's real value-correlation property (so there's still a small, meaningful marked set) while being a genuinely different graph topology from GHZ. Confirmed directly: n=3 gives support `{'000': 0.5, '110': 0.5}` — a real, different signature from GHZ's `{'000': 0.5, '111': 0.5}`, not the same thing renamed.

**Marked-bitstring definition, generalized:** rather than hand-picking a claim, "marked bitstrings" = the real, exact support of the *correct* circuit (computed via the stabilizer tableau), so the ideal circuit's claimed probability is always exactly 1.0 by construction — same convention as batch 1, just derived directly from each circuit's own real behavior instead of assumed.

## Mutation operators and measured classification

Same two operator types as batch 1, applied fresh to this circuit, classified by **measured** effect size (not assumed to match batch 1's assignment):

| Operator | n | Mean \|Δexact marked prob\| | Min | Max |
|---|---|---|---|---|
| ADD (extra Clifford gate at random position) | 120 | 1.0000 | 1.0000 | 1.0000 |
| REMOVE/REPLACE (weaken one entangling gate) | 24 | 0.5000 | 0.5000 | 0.5000 |

Same split as batch 1: ADD → **obvious** class (unambiguous, drives marked probability to exactly 0), REMOVE/REPLACE → **subtle** class (lands at exactly 0.5 — again).

## Why this isn't a coincidence — the mechanism, worked out this time

Landing at *exactly* 0.5 on a structurally different topology is not a fluke; it's a general property of this mutation type on any circuit built this way. The construction is: one `H` creates a clean two-way split (the qubit that got the `H` is 0 in one branch, 1 in the other), then a chain of `CX` gates propagates that split to every other qubit, so the circuit's whole behavior is exactly two equally-likely outcomes. Removing or weakening any single `CX` link breaks the propagation to exactly one qubit — but the original two-way split from the `H` is completely undisturbed, so the two branches stay exactly 50/50. One whole branch keeps its correct pattern (stays inside the marked set); the other branch now has one wrong bit and falls completely outside it. Net effect: exactly 0.5 marked probability, regardless of whether the CX gates are arranged as a star (GHZ) or a ring (this batch) — the topology doesn't matter, only the mutation type does.

**This means batch 1's boundary-landing wasn't a GHZ-specific artifact. It's a structural consequence of "weaken one entangling link in an H-seeded CX-propagation circuit," which reproduces on a different topology exactly as this mechanism predicts.** That's a stronger, more general finding than "GHZ happened to be hard" — it identifies *which class of mutation* is structurally hard for this kind of circuit, independent of topology.

## Results

Sample size, reported honestly: 120 obvious mutants (uncapped), **24 subtle mutants** (structural ceiling for this design — 3+4+5=12 CX positions across n=3,4,5, times 2 operators = 24; slightly larger than batch 1's 18, since a ring has one more edge than GHZ's star at the same qubit count).

### Detection rates

| Class | Shots | n | OLD detected | NEW detected |
|---|---|---|---|---|
| obvious | 1024 | 120 | 100.0% | 100.0% |
| obvious | 8192 | 120 | 100.0% | 100.0% |
| subtle | 1024 | 24 | 66.7% | **12.5%** |
| subtle | 8192 | 24 | 50.0% | **4.2%** |

### False-positive rate (unmutated controls, n=90 per shot count)

| Shots | OLD | NEW |
|---|---|---|
| 1024 | 0.00% | 0.00% |
| 8192 | 0.00% | 0.00% |

### Verdict breakdown on subtle-class misses

| Shots | VERIFIED (confidently wrong) | FAIL (detected) | INCONCLUSIVE (honest "not enough data") |
|---|---|---|---|
| 1024 | **1 (4.2%)** | 3 (12.5%) | 20 (83.3%) |
| 8192 | 0 (0.0%) | 1 (4.2%) | 23 (95.8%) |

**One honest difference from batch 1, reported precisely, not smoothed over:** batch 1 had zero confidently-wrong VERIFIED verdicts at either shot count. This batch had **one** (4.2%, at 1024 shots) — a single mutant where the new test confidently declared equivalence on a real mutant. Small sample (n=24), so this could be ordinary sampling variation rather than a real behavioral difference, but the honest report is "mostly still safe, not perfectly safe this time," not "identical to batch 1."

## Judgment against the locked criteria

**Criterion 1 (comparative):** obvious ties (100%=100%). Subtle: NEW (12.5%/4.2%) substantially worse than OLD (66.7%/50.0%) at both shot counts. **Answer: NO** — same as batch 1.

**Criterion 2 (floors):** obvious clears 85% (100% ≥ 85%). Subtle does not clear 45% at either shot count (12.5%/4.2% < 45%) — same as batch 1.

**Criterion 3 (false-positive ceiling ≤2%):** both checks, both shot counts: 0.00%. **Answer: YES** — same as batch 1.

**Outcome, per Section 4 of the locked pre-registration:** same as batch 1 — the new test does not beat the old one, stays informational-only.

## The comparison this batch exists to make

**Did the same pattern from batch 1 show up here, or did this circuit behave differently?**

**The same pattern showed up.** On a structurally different topology (ring vs. star), the new test still loses to the old one specifically on the subtle class, still clears the obvious-class floor easily, still has zero false positives, and still fails mostly safely rather than confidently — 83–96% of its subtle-class misses were honest INCONCLUSIVE, versus 100% in batch 1. The one real difference is a single confidently-wrong verdict this time (4.2% at 1024 shots, zero at 8192) — worth noting honestly rather than claiming a perfect match, but not enough to change the overall shape of the result.

**What this means for interpreting batch 1:** the negative result is not an artifact of GHZ's specific shape. It reproduces, with a mechanistic explanation, on a genuinely different circuit — which makes "the new test underperforms on subtle, single-entangling-gate-weakening mutations" a more general, more trustworthy finding than it was after batch 1 alone.
