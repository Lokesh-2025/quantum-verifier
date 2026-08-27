# Pre-registration: mutation-testing study for the equivalence test
Date: 2026-08-27. Locked before any mutation-study code exists.

## Purpose
Decide, in advance, whether the new equivalence test (replacing the old
tolerance-band check) actually detects more real bugs than what it replaced.
Written down now so the pass/fail bar can't be adjusted after seeing results.

## 1. Pass criterion (comparative)
The new equivalence test must perform at least as well as the old
tolerance-band check on EVERY mutation class tested, at matched
false-positive rate. Doing worse on even one class is not a pass -- that
would be a trade-off requiring its own separate discussion, not a result
that gets averaged away.

## 2. Absolute floor
- Clearly broken circuits (large/obvious mutations -- wrong gate type, wrong
  wiring, major structural changes): must catch at least 85% to be
  considered trustworthy enough to say "this passed our gate."
- Subtle mutations (a single small change, e.g. one gate swapped): expected
  to be genuinely harder to catch statistically. Floor set at 45%. A low
  number here is informative on its own, not a failure of the exercise.

## 3. False-positive ceiling
No more than 2% (1 in 50) false-positive rate on known-good, unmutated
controls. Set this tight because a false block costs real IonQ credits and
queue time, not just a few minutes of annoyance.

## 4. If the new test does not beat the old one
It stays informational-only, indefinitely. This is NOT treated as failure.
It gets written up and published as an honest negative result. It will not
be promoted to a blocking check unless it meets criterion 1.

## 5. Parked, not resolved here
The boundary type-I rate measured 2026-08-27 (false-VERIFIED rate 0.05131 at
100k trials -- anti-conservative, +1.9 SE above alpha) is a known open
finding. It may eventually justify switching from the Wilson confidence
interval to Clopper-Pearson for any check that graduates to blocking. Not
decided here -- revisit only if/when graduation is actually considered.

## Amendment rule
Changing any number above requires a new, dated commit with its own stated
reasoning -- never a silent edit to this file.
