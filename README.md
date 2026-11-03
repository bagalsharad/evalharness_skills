# skillcheck — regression testing for Agent Skills

Agent Skills (folders with a `SKILL.md`, scripts and references) change how an agent behaves, but they rarely
get tested. They fail silently in three ways:

1. **Skill edit regression**: a change to the instructions, scripts or description breaks something that used to work.
2. **Model drift**: a new model or snapshot triggers or follows the same skill differently.
3. **Interaction regression**: adding a skill steals prompts from, or changes the behavior of, another one.

skillcheck is a set of **Claude Code skills** that catch these on your own machine. It runs your skill in real
Claude Code sessions, several trials per test, and reports **verdicts with confidence intervals**, not raw pass
rates, because a model is stochastic and one run proves nothing.

| Skill | What it does |
|---|---|
| `skillcheck` | Walks through the whole workflow and hands off to the others. |
| `skillcheck-lint` | Spec checks, content hash, context-token footprint, description collisions across a library. Free. |
| `skillcheck-suite` | Scaffolds and validates test suites (trigger, near-miss, behavior, adversarial, interaction, budget); mutation testing. |
| `skillcheck-run` | Runs trials with `claude -p` in isolated temp projects; traces, graders, LLM judges, cost caps, resume. |
| `skillcheck-compare` | Statistics, regression/lift verdicts, CI gate, baselines, drift alerts, HTML/Markdown/JUnit reports. |

Everything is stdlib Python 3.9+. PyYAML is needed only for YAML files (`uv run` installs it automatically).

## Install (2 minutes)

```bash
git clone <this repo> skillcheck && cd skillcheck
./install.sh                      # personal: ~/.claude/skills, available in every project
# or
./install.sh --project ~/my-repo  # project: ~/my-repo/.claude/skills, commit it to share with your team
```

Or install it as a Claude Code plugin from this folder (skills are then namespaced `skillcheck:…`):

```
/plugin marketplace add /path/to/skillcheck
/plugin install skillcheck@skillcheck-marketplace
```

Prerequisites: Claude Code (`claude` on PATH, or set `SKILLCHECK_CLAUDE_BIN`), Python 3.9+, and optionally
PyYAML and Docker. Check them with:

```bash
python3 ~/.claude/skills/skillcheck-run/scripts/run_suite.py doctor
```

## 5-minute quickstart

Open Claude Code in the repo that holds your skill and say:

> Use skillcheck to test my skill at `skills/my-skill`.

Claude will lint the skill, draft a suite in `skillcheck-tests/my-skill/` for you to review, show you a cost
estimate, run a smoke test, and explain the results. The same steps by hand:

```bash
SC=~/.claude/skills
python3 $SC/skillcheck-lint/scripts/lint_skill.py skills/my-skill
python3 $SC/skillcheck-suite/scripts/scaffold_suite.py skills/my-skill          # then edit the TODOs
python3 $SC/skillcheck-suite/scripts/validate_suite.py skillcheck-tests/my-skill/suite.yaml
python3 $SC/skillcheck-run/scripts/run_suite.py run skillcheck-tests/my-skill/suite.yaml --dry-run
python3 $SC/skillcheck-run/scripts/run_suite.py run skillcheck-tests/my-skill/suite.yaml --run-id v1
python3 $SC/skillcheck-compare/scripts/analyze.py summary .skillcheck/runs/v1
python3 $SC/skillcheck-compare/scripts/analyze.py baseline save main .skillcheck/runs/v1

# ...edit the skill, then:
python3 $SC/skillcheck-run/scripts/run_suite.py run skillcheck-tests/my-skill/suite.yaml --run-id v2
python3 $SC/skillcheck-compare/scripts/analyze.py compare baseline:main .skillcheck/runs/v2
python3 $SC/skillcheck-compare/scripts/report.py .skillcheck/runs/v2 --html report.html
```

Try the bundled examples first: `examples/suites/haiku-poet` (a formatting skill), `examples/suites/csv-summary`
(a script-backed skill) and `examples/suites/interaction` (three skills competing for prompts).

## What a test looks like

```yaml
spec_version: 1
suite: csv-summary
skills: [../../skills/csv-summary]
defaults: {trials: 5, allow_tools: ["Bash(python3 *)"]}
tests:
  - id: no-trigger-write-csv          # near-miss: shares words, needs something else
    kind: trigger
    expect_trigger: false
    prompt: Create a CSV file called people.csv with three made-up names and ages.
  - id: correct-summary
    kind: behavior
    critical: true
    prompt: Summarize sales.csv and write the stats to summary.json
    fixtures: [fixtures/sales.csv]
    graders:
      - {type: trace, must_load_skill: csv-summary, required_tools: ["Bash(*summarize.py*)"]}
      - {type: python, entrypoint: graders/check_summary.py}   # recomputes the right answer
```

Full format: [skills/skillcheck-suite/references/spec-v1.md](skills/skillcheck-suite/references/spec-v1.md).

## What the output looks like

```
## skillcheck comparison
**Gate: ❌ BLOCKED**
- ❌ critical test `writes-report` regressed -83 pts (q=0.030)
- ⚠️ critical test `trig-yes` is inconclusive (can only detect a 53-pt change with these trials)

| test | kind | base | cand | change | 95% CI | q | verdict |
| `writes-report` ⭐ | behavior | 6/6 | 1/6 | -83 pts | -97…-28 | 0.030 | 🔴 regression |
| `trig-yes` ⭐ | trigger | 6/6 | 6/6 | +0 pts | -39…+39 | 1.000 | 🟡 inconclusive |
```

Verdicts are `regression`, `improvement`, `no_significant_change`, or `inconclusive` (too few trials to rule out
the change you care about). The methods are Fisher exact per test, Benjamini-Hochberg FDR within critical, gated
and informational families, and a paired bootstrap at suite level. They were cross-checked against SciPy and
statsmodels and calibrated by A/A simulation. See
[statistics.md](skills/skillcheck-compare/references/statistics.md).

## Design choices

- **Real runtime.** Trials run the real Claude Code CLI in headless mode (`claude -p --output-format stream-json`).
  Skill activation is read from the trace (the `Skill` tool call), verified against Claude Code 2.1.x.
- **Isolation.** Each trial gets a fresh temp project containing only the fixtures and the skills under test,
  with project-only settings, a minimal tool set, web tools disabled, Bash not pre-approved, and per-trial and
  per-run cost caps. This is not a security sandbox: read
  [isolation.md](skills/skillcheck-run/references/isolation.md) before testing skills you did not write.
- **Tamper-evident runs.** Each run is an append-only, hash-chained event log; `results.json` is derived from
  it and records skill content hashes, git SHAs, the suite hash, the CLI version and the resolved model ids.
- **Honest gating.** Unreviewed generated tests, quarantined tests and uncalibrated LLM judges are reported
  but never gate. Infrastructure errors are retried and excluded, never counted as failures.
- **No telemetry.** Nothing leaves your machine except the model calls Claude Code makes, and the webhook if
  you configure one.

## CI and drift

[skills/skillcheck-compare/assets/skillcheck-ci.yml](skills/skillcheck-compare/assets/skillcheck-ci.yml) is a
GitHub Actions workflow. On each PR it runs the base-branch skill against the PR skill and gates with a policy
file. Each night it compares a pinned model snapshot with the floating alias and sends de-duplicated alerts to
a webhook. See [ci-and-drift.md](skills/skillcheck-compare/references/ci-and-drift.md).

## Related

Claude Code also ships `claude plugin eval`, which scores eval cases against a plugin with and without it.
skillcheck focuses on skills at the folder level: regression verdicts with statistics, trigger
precision/recall, interaction tests, baselines, tamper-evident runs and drift monitoring.

## Development

```bash
python3 -m unittest discover -s tests -v   # 36 tests, offline: a fake claude CLI stands in for the real one
python3 tools/sync_core.py                 # after editing core/skillcheck_core.py (each skill carries a copy)
```

`core/skillcheck_core.py` is the shared library (parsing, hashing, lint, suites, statistics, hash chain). Each
skill ships its own copy so it works on its own after install; a test fails if the copies drift.
Design notes, assumptions and known gaps are in [docs/design.md](docs/design.md).

License: Apache-2.0.
