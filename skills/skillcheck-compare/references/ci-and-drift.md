# CI gating and drift monitoring

## Pull-request gate

[assets/skillcheck-ci.yml](../assets/skillcheck-ci.yml) is a GitHub Actions workflow. On each PR that
touches the skill or its tests, it:

1. runs the suite against the **base branch's** skill and the **PR's** skill in the same job, with the same
   pinned model and the same trial count (a fair comparison, and no stored baseline needed);
2. runs `analyze.py gate` with your `gate.yaml` ([example](../assets/gate.example.yaml));
3. posts the Markdown verdict table as a PR comment, uploads the HTML report, JUnit XML and raw runs, and fails
   the check when the gate blocks.

Cost is about 2 × tests × trials × cost per trial. To keep PR checks cheap, use `--tags smoke` there and the
full suite nightly.

GitLab CI: the same steps run in any job with Python 3.9+ and the Claude Code CLI. Use `analyze.py gate`'s
exit code and publish `junit.xml` as a JUnit report artifact.

## Drift monitoring

Drift means the same skill behaves differently because the model or serving changed. The nightly job in the
workflow runs two arms on the same night:

- **control**: a pinned, dated model snapshot;
- **canary**: the floating alias your users actually get.

`analyze.py drift control canary` compares them and updates the alert state in `.skillcheck/drift/state.json`.
A comparison against a stored baseline also works: `analyze.py drift baseline:main <tonight>`.

### Alert state machine

| State | Entered when | Notifies? |
|---|---|---|
| open | a signal shows a significant regression and was not already open | yes (webhook) |
| open (repeat) | the same signal regresses again | no; the counter goes up |
| acknowledged | `analyze.py drift-ack <key>` | no |
| resolved | the signal is no longer a regression | no |

Signals: each test's verdict, trigger precision and recall per skill, and the suite verdict. The webhook
receives `{"text": "..."}`, which Slack incoming webhooks accept directly. For PagerDuty, point it at an
Events API v2 bridge or a small relay. Set `--webhook` or `SKILLCHECK_WEBHOOK`.

### Local schedule (macOS launchd or cron)

```bash
# nightly.sh
set -euo pipefail
cd /path/to/your/skills-repo
SC=~/.claude/skills                 # where the skillcheck skills are installed
TODAY=$(date +%Y%m%d)
python3 $SC/skillcheck-run/scripts/run_suite.py run skillcheck-tests/my-skill/suite.yaml \
  --model sonnet --trials 10 --max-run-usd 10 --run-id nightly-$TODAY --quiet
python3 $SC/skillcheck-compare/scripts/analyze.py drift baseline:main .skillcheck/runs/nightly-$TODAY \
  --webhook "$SKILLCHECK_WEBHOOK" --md .skillcheck/drift/latest.md
```

cron: `17 3 * * * /bin/bash /path/to/nightly.sh >> ~/skillcheck-nightly.log 2>&1`
(cron jobs do not see your login keychain; set `ANTHROPIC_API_KEY` in the script's environment.)

## Runbook

| Symptom | Likely cause | Action |
|---|---|---|
| Many `error` trials, gate blocks on error rate | provider outage or rate limits | Check provider status; lower `--concurrency`; re-run or `resume`. Errors never count as failures. |
| A/A comparison shows regressions | nondeterminism plus too few tests, or unrecorded drift | Raise trials; look at `changed_axes`; rerun A/A. A persistent A/A regression points to real drift. |
| Judge-graded tests flip with no skill change | judge drift | Re-run `calibrate-judge` on the frozen labeled set; pin the judge model; keep judge tests out of the gate. |
| Lots of tests suddenly flaky | model or runtime update, or an ambiguous test | `analyze.py flaky` over recent runs; `SHIFTED` = a change, `QUARANTINE?` = a flaky test. |
| Cost spike | skill body bloat, a looping agent, a model change | Compare `$ / trial` and `median_tool_calls`; check the lint footprint; add `budget` tests. |
| Run hit the budget cap | estimate too low | `--dry-run` with a better `--est-cost-per-trial` (from `totals.cost_usd / totals.trials`); `resume` with a higher cap. |
| Sandbox or grader failures | Docker daemon down, image pull blocked | `run_suite.py doctor`; pre-pull `python:3.12-slim`. |
