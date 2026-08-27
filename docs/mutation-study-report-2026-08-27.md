# First mutation-testing batch: results, judged against the pre-registered criteria

Date: 2026-08-27. Judged against `docs/pre-registration-mutation-study-2026-08-27.md` (commit `42e1e16`, locked before this study's code existed). Criteria are not re-derived here — they stand exactly as pre-registered.

**Scope, stated plainly up front: this result is scoped to GHZ-family circuits with a small sample (120 obvious-class mutants, 18 subtle-class mutants). It is not a general claim about the new equivalence test's real-world performance across circuit types.** Treat it as a first, honest data point, not a verdict on the check in general.

Harness: `scripts/mutation_study_2026-08-27.py`. Fully reproducible (seeded mutant selection *and* seeded simulator shots — verified by running twice and diffing identical output).

---

## Step 1 — Corpus availability

The academic "Quantum Circuit Mutants" corpus (723k mutants, 382 circuits; Mendiluze Usandizaga et al., EMSE 2025, arXiv:2311.16913) is **not available** in this environment and not trivially fetchable:

- No local copy anywhere in this environment (full filesystem search).
- No dataset link in the paper itself or its associated tool's GitHub repo (`Simula-COMPLEX/muskit`) — checked both directly.
- The mutation-*generation* tool from the same team, `muskit`, **is** pip-installable (26KB package) — but that's source code for generating new mutants on demand, not the paper's actual 382-circuit corpus with its specific 723k pre-generated mutants.

Per the fallback instruction, mutants were built from this project's own known-answer templates instead — specifically `core/templates.py`'s `ghz_parity_check_circuit`, whose ground truth is checkable exactly (not simulated) via `core/stabilizer.py`'s stabilizer-tableau computation.

## Step 2 — Batch construction

**Checks under test:** `core/verifier.py`'s `ground_truth_check` (OLD, the tolerance-band heuristic) vs. `ground_truth_significance_test` (NEW, the TOST equivalence test). Nothing else.

**Circuits:** GHZ at n=3, 4, 5 qubits.

**Mutation operators** (Clifford-only, so every mutant's *exact* ground truth stays computable via the stabilizer tableau rather than needing to be assumed):

- **ADD** — insert one extra single-qubit Clifford gate ({X, Y, Z, S}) at a random position.
- **REMOVE** — delete one entangling (CX) gate.
- **REPLACE** — swap one CX for CZ.

**A real correction made mid-build, reported not hidden:** the first version of this script classified "obvious" vs. "subtle" by guessing which edit sounded like a bigger change (remove/replace-the-seed-gate → obvious; replace-CX/add-gate → subtle). The *measured* exact-probability effect sizes came back backwards — the intended-subtle class had a larger real effect (mean Δ=0.96) than the intended-obvious class (mean Δ=0.50). Reclassified by measured effect size instead of guessed effect size, and reran the batch clean under the corrected assignment before recording any results:

- **obvious** = ADD mutations. Measured to drive the exact marked probability to 0 every time (mean Δ=1.0000, min=max=1.0000) — an unambiguous "this circuit no longer produces the claimed signature."
- **subtle** = REMOVE / REPLACE-CX-with-CZ mutations. Measured to land the exact marked probability at *exactly* 0.5 every time (mean Δ=0.5000, min=max=0.5000), regardless of which CX or how many qubits — a real structural property of GHZ's binary branching (the |0…0⟩ branch is always untouched by removing/weakening one downstream CX, while the |1…1⟩ branch always loses exactly that one qubit's correlation), not an artifact of this script. 0.5 sits exactly at the historical `amplification_tolerance=0.5` default's boundary — genuinely the hardest case to detect, even though it isn't a smaller circuit edit than the obvious class.

**Sample size — did not reach "a few thousand," reported honestly:** 120 obvious mutants (uncapped — ADD is randomized over position/qubit/gate, so this scales freely) and only **18 subtle mutants** (a hard structural ceiling for this design: with only REMOVE/REPLACE-CX-with-CZ as subtle operators, and only 2+3+4=9 distinct CX positions across n=3,4,5, there are exactly 18 distinct non-equivalent subtle mutants possible — not a sampling shortfall, an exhausted operator space). Scaling the subtle class to thousands would need either a broader operator set or many more circuit templates — out of scope for this bounded first batch per instructions.

Equivalent mutants (no real behavioral change — e.g. replacing the seed H with X was tried and found to *always* leave the exact marked probability at 1.0, since it deterministically lands on the other marked bitstring) were excluded from both classes.

**Shot counts:** 1024 and 8192, simulator only (`qiskit_aer.AerSimulator`, purely local, zero network calls, zero cost — not IonQ's cloud simulator, which requires a real API key and submits real jobs even on its free tier).

## Step 3 — Results and judgment

### Detection rates

| Class | Shots | n | OLD detected | NEW detected |
|---|---|---|---|---|
| obvious | 1024 | 120 | 100.0% | 100.0% |
| obvious | 8192 | 120 | 100.0% | 100.0% |
| subtle | 1024 | 18 | 72.2% | **5.6%** |
| subtle | 8192 | 18 | 50.0% | **0.0%** |

### False-positive rate (unmutated controls, n=90 per shot count)

| Shots | OLD | NEW |
|---|---|---|
| 1024 | 0.00% | 0.00% |
| 8192 | 0.00% | 0.00% |

### What kind of miss is driving the subtle-class number

The raw detection-rate gap above doesn't say whether the new test's misses are dangerous (confidently wrong) or safe (honestly uncertain) — checked directly, per an explicit follow-up request, before writing this up:

| Class | Shots | VERIFIED (confidently wrong) | FAIL (detected) | INCONCLUSIVE (honest "not enough data") |
|---|---|---|---|---|
| obvious | 1024 | 0.0% | 100.0% | 0.0% |
| obvious | 8192 | 0.0% | 100.0% | 0.0% |
| subtle | 1024 | **0.0%** | 5.6% | 94.4% |
| subtle | 8192 | **0.0%** | 0.0% | 100.0% |

**Across the entire run, the new test never once confidently declared a subtle mutant "VERIFIED" (equivalent) when it shouldn't have.** Every miss on the subtle class was INCONCLUSIVE — an honest "not enough shots to resolve this," not a confident wrong answer. The raw detection-rate numbers above are a real negative result against the locked criteria, but the *failure mode* behind that number is the safe one: the new test abstains rather than asserts, exactly where it lacks the evidence to be sure.

### Judgment against the locked criteria

**Criterion 1 (comparative — must match-or-beat OLD on every class):**
- obvious: 100% = 100% — ties. ✓
- subtle: NEW (5.6% / 0.0%) is substantially worse than OLD (72.2% / 50.0%) at **both** shot counts — not a marginal gap.
- **Answer: NO.** Per the pre-registration's own wording, doing worse on even one class is not a pass.

**Criterion 2 (absolute floors — 85% obvious / 45% subtle):**
- obvious: 100.0% ≥ 85% — **clears the floor.**
- subtle: 5.6% and 0.0%, both far below 45% — **does not clear the floor.**
- Per the pre-registration's own wording, a low subtle number is informative on its own, not a failure of the exercise.

**Criterion 3 (false-positive ceiling ≤2%):**
- OLD: 0.00% at both shot counts.
- NEW: 0.00% at both shot counts.
- **Answer: YES**, both checks.

### Outcome, per Section 4 of the locked pre-registration

The new test does not beat the old one (fails criterion 1 on the subtle class). Per the pre-registered consequence: **it stays informational-only, indefinitely** — exactly its current, already-shipped state (it has never been wired to block `verify()`'s verdict). This is not treated as a failure of the project; it is the honest negative result the pre-registration existed to let be reported honestly rather than rationalized away.

## What this does and doesn't tell us

**Does tell us:** on GHZ-family circuits, at the exact historical tolerance boundary, the new equivalence test is more conservative about declaring equivalence than the old tolerance-band check — and that conservatism manifests as honest uncertainty, not confident error, in every single case measured here.

**Does not tell us:** how either check performs on non-GHZ circuit structures, on mutation classes between "flips one bit everywhere" and "sits exactly at the tolerance boundary," at shot counts beyond 1024/8192, or at a sample size large enough to put a tight confidence interval on the subtle-class numbers (n=18 is small, and it's a genuine structural ceiling for this circuit family, not a choice). A follow-up batch with a broader circuit/operator base is a real, separate, later decision — not implied or started here.
