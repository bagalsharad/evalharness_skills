# Statistics design

Goal: say "regression" only when the data supports it, and say "inconclusive" when the trials cannot tell.
Priority order: correct claims, then reproducibility, then speed.

## Units

- A **trial** is one independent agent run of one test. Outcome: pass (1) or fail (0); infrastructure errors
  are excluded, and their count is reported.
- A **test** has n graded trials with k passes. The estimate is p̂ = k/n.
- Trials of the same test are treated as i.i.d. Bernoulli(p). The same prompt still gives different
  outcomes because sampling and serving are nondeterministic, and no seed controls that in Claude Code
  today. That is why several trials are needed.

## Per-run metrics

| Metric | Formula | Notes |
|---|---|---|
| Pass rate CI | Wilson score: (p̂ + z²/2n ± z√(p̂(1-p̂)/n + z²/4n²)) / (1 + z²/n) | Good coverage at small n and at p̂ = 0 or 1, unlike the Wald interval. |
| pass@k | 1 - C(n-c, k) / C(n, k) | Unbiased estimate of P(at least one of k fresh trials passes) (Chen et al. 2021). "Can it ever do this?" |
| pass^k | C(c, k) / C(n, k) | Unbiased estimate of P(all k fresh trials pass) (tau-bench, Yao et al. 2024). "Can I rely on it?" Reported with k = min(3, n). |
| Trigger precision | TP / (TP + FP) over trigger-test trials | FP: the skill loaded on a should-not-trigger prompt. |
| Trigger recall | TP / (TP + FN) | FN: the skill did not load on a should-trigger prompt. |
| Interaction accuracy | correct selections / trials | Wilson CI. |

## Comparing a candidate with a baseline

**Per test (two independent samples).** Baseline and candidate trials are separate random draws, and with
no seed control there is no true pairing between "trial 3" and "trial 3". So per-test comparisons are
unpaired:

- Test: two-sided **Fisher exact** on [[k_b, n_b-k_b], [k_c, n_c-k_c]]. It is exact and conservative at small n.
- Effect: Δ = p̂_c - p̂_b, with the **Newcombe hybrid-score** 95% CI (Newcombe 1998, method 10).
- Multiplicity: **Benjamini-Hochberg** FDR at α, applied within three families:
  `critical` tests, other `gated` tests, and `informational` tests (quarantined, unreviewed, or graded by an
  uncalibrated judge). Tests that never gate then do not dilute the power of tests that do, and critical
  tests are judged on their own, as the gate requires.

**Suite level (paired by test).** The same tests run on both sides, so tests are the pairing unit.
- d_i = p̂_c,i - p̂_b,i for each gated test i (quarantined and unreviewed tests excluded).
- Mean of d with a **paired percentile bootstrap** CI (10,000 resamples of tests, fixed seed, so the result is
  reproducible). The p-value comes from the shift-to-null bootstrap. An **exact sign test** (McNemar on the tests
  that went down vs up) is reported alongside.
- With fewer than 5 tests the suite verdict gets a `_weak` suffix: bootstrapping over a handful of tests
  understates the uncertainty.

The brief suggested McNemar for paired binary outcomes. McNemar needs real pairs. Here, pairs exist only at
the test level, which is where the sign test applies it. Pairing trial i with trial i inside a test would
invent a pairing that does not exist and overstate confidence.

## Verdict rule

For each test (and for the suite with its own inputs):

1. q < α and Δ < 0 → `regression`
2. q < α and Δ > 0 → `improvement`
3. otherwise, if the CI for Δ lies inside (-min_effect, +min_effect), or the minimum detectable effect
   ≤ min_effect → `no_significant_change`
4. otherwise → `inconclusive` (underpowered)

Tests with fewer than 2 graded trials on either side are always `inconclusive`.

## Power and minimum detectable effect

MDE ≈ (z₁₋α/₂ + z_power) · √(p̃(1-p̃)(1/n_b + 1/n_c)), with p̃ = (k_b+1)/(n_b+2) (Laplace smoothing, so a
10/10 baseline does not claim infinite sensitivity). This normal approximation is advisory: it is
conservative at small n, and Fisher's test is itself conservative.

What this means in practice (α = 0.05, power 0.8):

| Baseline pass rate | Trials per side | Smallest detectable drop |
|---|---|---|
| 0.95 | 5 | ~0.68 |
| 0.95 | 10 | ~0.41 |
| 0.95 | 20 | ~0.25 |
| 0.95 | 30 | ~0.19 |

(`analyze.py power --p 0.95 --trials N` prints these.) Per-test gates cannot see 10-point drops at affordable
trial counts. Gate on the **suite** verdict, give critical tests more trials, and set `--min-effect` to what you
actually care about.

**Sequential testing.** Running more trials only because a result was borderline ("peeking") inflates false
positives. skillcheck v0.1 does not do adaptive allocation. Choose trial counts up front with `analyze.py power`.

## Drift alerts

Each signal (per-test verdict, trigger precision and recall per skill, suite verdict) has a state machine:
`open → acknowledged → resolved`. An alert fires only on a transition into `open`. The same regression
reported again the next night increments a counter but does not page again. When a later run no longer shows
the regression, the alert resolves itself.

## Validation (how we know the maths is right)

- **Reference values:** Wilson, Fisher, binomial and McNemar tests, Newcombe CI and BH were cross-checked
  against SciPy and statsmodels on 300+ random tables (max absolute error < 1e-12). Fixed reference values are
  pinned in `tests/test_skillcheck.py`.
- **A/A simulation:** 3,000 simulated A/A comparisons (n=10 per side, p ∈ {0.5, 0.7, 0.9}) produce a
  `regression` verdict at a rate ≤ α (a unit test enforces this).
- **Detection simulation:** a 95% → 45% drop at 20 trials per side is flagged in > 90% of simulations.
- **Real A/A:** run the same suite twice against the same skill and model, then
  `analyze.py compare run1 run2`. With nothing changed, any `regression` is a false positive; track the rate
  over time.
- **Mutation benchmark:** `skillcheck-suite/scripts/mutate_skill.py` builds realistic broken skills. The
  detection rate per mutant type measures how strong your suite is.

## Assumptions and limits

- Trials are independent. Prompt caching or a provider incident during a run can correlate them.
  `model_changed_mid_run` flags one case of this.
- Pass/fail is binary. Continuous judge scores are averaged per test and reported, but verdicts use the binary
  outcome.
- Cost comparisons use the paired bootstrap on per-test mean cost per trial (judge cost excluded).
