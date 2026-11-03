---
name: skillcheck-compare
description: Analyzes skillcheck runs with sound statistics - pass rates with confidence intervals, pass@k and pass^k, trigger precision and recall, regression or improvement verdicts against a baseline (Fisher exact tests, false-discovery control, power checks), skill lift, CI gating, pinned baselines, model-drift alerts, flaky-test detection and HTML, Markdown or JUnit reports. Use when the user asks whether a skill regressed, wants to compare two runs, gate a pull request, check model drift, or get a report of skill test results.
license: Apache-2.0
compatibility: Needs Python 3.9+. PyYAML only for YAML gate policies.
---

# skillcheck-compare

Turns runs from **skillcheck-run** into verdicts. Scripts: `${CLAUDE_SKILL_DIR}/scripts/analyze.py` and
`${CLAUDE_SKILL_DIR}/scripts/report.py` (stdlib Python, call with `python3`). A run is referenced by its folder
(`.skillcheck/runs/<id>`), its `results.json`, or `baseline:<name>`.

## Pick the command

| The user wants | Command |
|---|---|
| How did this run go? | `analyze.py summary RUN` |
| Did my change regress anything? | `analyze.py compare BASE CAND --md comparison.md` |
| Does the skill help at all? | `analyze.py compare NO_SKILL_RUN WITH_SKILL_RUN --lift` |
| A pass/fail CI check | `analyze.py gate BASE CAND --policy gate.yaml --md pr-comment.md` (exit 1 = blocked) |
| How many trials do I need? | `analyze.py power --p 0.9 --effect 0.2` (add `--trials 10` to see the current sensitivity) |
| Pin a reference run | `analyze.py baseline save main RUN`, then compare against `baseline:main` |
| Nightly drift check with alerts | `analyze.py drift baseline:main CAND --webhook $URL`; `drift-status`, `drift-ack KEY` |
| Which tests are flaky? | `analyze.py flaky RUN1 RUN2 ...` |
| Prove results were not edited | `analyze.py verify RUN` |
| Shareable report | `report.py RUN [--compare cmp.json] --html report.html --md report.md --junit junit.xml` |

Write comparison JSON with `--out cmp.json` to feed `report.py --compare`.

## How to explain results to the user

1. **Lead with the verdict, then the numbers.** For example: "No significant change overall (-3 points, 95% CI -12 to
   +6). One critical test, `fills-basic-form`, regressed from 10/10 to 4/10 (q=0.01)."
2. Use the four verdicts exactly: `regression`, `improvement`, `no_significant_change`, and `inconclusive`.
   `inconclusive` means there were too few trials to rule out a change of `--min-effect` (default 20 points).
   It is not a pass. Say how many trials would settle it (the comparison's power hint, or `analyze.py power`).
3. Never call a single trial a pass or a fail, and never compare raw pass rates without their intervals.
4. Name what changed between the runs (the `changed_axes` field: skill hash, model, runtime, suite,
   distractors). If nothing changed, it is an A/A check: any regression points to nondeterminism or
   provider-side drift.
5. Point out the summary's warnings: unreviewed tests, uncalibrated judges, skills outside the suite that were
   loaded, a model that changed mid-run, a high infrastructure error rate.
6. For a regression, open a failing trial's evidence (`trials/<test>/<n>/summary.json`, `final_output.txt`)
   and explain why it failed. Don't guess.
7. Lift: if the with-skill vs without-skill suite CI includes 0, the skill shows **no measurable lift**.
   Report that as a finding.

## Gate policy

Copy [assets/gate.example.yaml](assets/gate.example.yaml) and adjust. Defaults: block on a suite-level
regression or a regression in a critical test; warn on inconclusive critical tests; exclude quarantined,
unreviewed and uncalibrated-judge tests. A ready-made GitHub Actions workflow is in
[assets/skillcheck-ci.yml](assets/skillcheck-ci.yml). Setting up nightly drift checks (cron, launchd or CI
schedule) and alert routing is covered in [CI and drift](references/ci-and-drift.md).

## Statistics

The methods, formulas, assumptions and reference checks are in [statistics](references/statistics.md). Read it
before you defend a verdict or change `--alpha` or `--min-effect`.
