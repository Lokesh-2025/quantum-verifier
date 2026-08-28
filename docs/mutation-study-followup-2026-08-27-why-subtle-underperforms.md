# Follow-up: why does the new test underperform the old one on subtle mutations?

Bounded, ~1-hour investigation per an explicit follow-up request after both mutation-testing batches. **Conclusion: this is a genuine, irreducible statistical tradeoff, not a fixable bug. No fix attempted, no batch 3, per the instruction that governed this investigation.**

## The question

Both batches showed the new equivalence test detecting subtle mutations only 4–12% of the time, versus the old tolerance-band check's 50–72% — a large enough gap to warrant checking for a specific mechanical cause, not just accepting "rigor costs sensitivity" as an explanation.

## Checked in order, as directed

**1. Equivalence margin (δ) at p₀=1.0.** Confirmed directly: with the default `tolerance=0.5` and a claim of `p_claimed=1.0` (exactly what every subtle mutant in both batches was tested against), the equivalence margin is `[0.5, 1.0]` — half the entire probability space — and `one_sided: True` (the same unreachable-upper-bound regime from the earlier boundary fix, since the margin's upper edge sits exactly at 1.0).

**2. Holm-Bonferroni correction.** Confirmed directly: neither study script imports or calls `aggregate_significance`. Both call `ground_truth_significance_test` directly. **This hypothesis is ruled out — the correction isn't in this code path at all.**

**3. Asymmetry in the one-sided CI-containment test.** Checked with 200 independent samples of the exact real subtle-mutant circuit (true probability exactly 0.5, the margin's lower edge), at 8192 shots each:

| CI position vs. the 0.5 boundary | Verdict | Count | Rate |
|---|---|---|---|
| Straddles 0.5 | INCONCLUSIVE | 178/200 | 89.0% |
| Entirely below 0.5 | FAIL (detected) | 10/200 | 5.0% |
| Entirely above 0.5 | VERIFIED (confidently wrong) | 12/200 | 6.0% |

**No directional bias** — the two "confident" outcomes split almost evenly (5.0% vs. 6.0%), which is what you'd expect from pure sampling noise around a true value sitting exactly on the boundary, not from a hidden asymmetry bug.

## The mechanical cause, stated plainly

**Every subtle mutant in both batches has a true marked-probability of exactly 0.5 — which is *exactly* the equivalence margin's own boundary**, not a value near it. A correctly-calibrated confidence interval is *designed* to contain the true parameter value roughly 90% of the time (matching this test's α=0.05, two-sided-equivalent duality). When the true value sits exactly at a decision boundary, "contains the true value" and "straddles the boundary" are the same event — so the test reporting INCONCLUSIVE ~89% of the time here isn't a flaw, it's the direct, necessary, mathematically expected consequence of asking a properly-calibrated test to make a confident call about a value sitting exactly on its own threshold. The ~5% detection rate is close to what α=0.05 alone would predict, and the ~6% confidently-wrong rate is its near-mirror. Nothing here points to a bug in δ, in a correction that isn't even applied, or in an asymmetry — the numbers are exactly what calibration theory predicts.

**Why the old check "wins" here isn't a real advantage.** It has no confidence margin at all — it's a bare point-estimate comparison against a fixed threshold. With the true value sitting exactly on that threshold, its observed sample will land above or below purely from sampling noise, roughly like a coin flip. Its higher "detection rate" on this specific mutation class isn't correctly identifying a real bug more often — it's winning a coin flip more often, with no statistical backing behind the call either way. That's the exact anti-pattern this whole project's rigor work has been correcting elsewhere (see: the original wrong-null-hypothesis bug, and the boundary type-I rate measurement from the overnight report, which found essentially the same phenomenon from a different angle).

## Is there a real fix worth trying?

**No — checked, and there isn't one that wouldn't make things worse elsewhere.** The only way to raise the detection rate here would be to make the interval less statistically cautious (a lower confidence level, a narrower margin not justified by the actual tolerance setting) — which would directly reintroduce the over-eager, anti-conservative flagging behavior this project has already spent real effort eliminating. This specific mutation class (weaken one entangling link in an H-seeded propagation circuit) happens to construct a true value that lands exactly on the historical `tolerance=0.5` default's own boundary — that's a property of *this test scenario*, not something a code fix to the equivalence test itself can resolve without trading away calibration correctness elsewhere.

**Conclusion, per the governing instruction: this is a genuine, irreducible statistical tradeoff of the equivalence-test framing at an exact decision boundary, not a fixable bug. No fix attempted. No batch 3.**

## What this changes, and what it doesn't

Does not change the verdict from either mutation-testing report — the new test still stays informational-only, exactly as both reports concluded, per the pre-registration's own rule. What it does add: the reason for the negative result is now understood precisely, not just observed. And it sharpens what "the new test underperforms" actually means in practice — it's specifically weak at confidently flagging cases that sit *exactly* on the configured tolerance boundary, which is a real, useful, narrower characterization than "worse at subtle bugs in general." A mutation whose true effect landed clearly outside the boundary (not exactly on it) would very likely be a different story — this investigation doesn't cover that case, and extending it would be new scope, not part of this bounded follow-up.
