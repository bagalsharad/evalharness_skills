---
name: skillcheck-run
description: Runs an Agent Skill's test suite (suite.yaml) against Claude Code headless mode - several trials per test, each in a fresh isolated project, with full trace capture, deterministic/trace/LLM-judge grading, cost caps and resumable runs. Also runs with the skill removed to measure lift. Use when the user wants to execute skill tests or evals, run trials of a skill, check a skill on a specific model, or resume an interrupted skillcheck run.
license: Apache-2.0
compatibility: Needs Python 3.9+ and the Claude Code CLI (claude) on this machine. PyYAML or uv for YAML suites. Docker optional.
---

# skillcheck-run

Executes a suite and writes an immutable, hash-chained run log plus `results.json`.
The runner is `${CLAUDE_SKILL_DIR}/scripts/run_suite.py` (stdlib Python; call it with `python3`).

## Before the first run

1. Check the setup: `python3 ${CLAUDE_SKILL_DIR}/scripts/run_suite.py doctor`
   - If it reports PyYAML missing and the suite is YAML, use `uv run ${CLAUDE_SKILL_DIR}/scripts/run_suite.py ...`
     (uv installs PyYAML from the script header) or `pip install pyyaml`.
2. Validate the suite: `python3 ${CLAUDE_SKILL_DIR}/scripts/run_suite.py validate path/to/suite.yaml`
3. **Always do a dry run and show the user the trial count and cost estimate before spending money:**
   `python3 ${CLAUDE_SKILL_DIR}/scripts/run_suite.py run path/to/suite.yaml --dry-run`
   Ask the user to confirm if the estimate is above about $2 or they have not approved a budget.

## Run

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_suite.py run path/to/suite.yaml \
  [--trials 5] [--model claude-sonnet-4-5] [--tags smoke] [--test ID ...] [--kind trigger] \
  [--concurrency 3] [--max-run-usd 10] [--out .skillcheck/runs] [--run-id NAME]
```

- Runs take minutes. Run the command in the background if your environment allows it, and tell the user
  progress is printed per trial (PASS/FAIL/TIME/ERR with running cost).
- The skills under test come from `skills:` in the suite (or `--skill PATH`, repeatable).
- Quick local loop for a skill author: `--tags smoke --trials 3`.
- Script-backed skills need Bash pre-approved: put `allow_tools: ["Bash(python3 *)"]` in the suite
  defaults or pass `--allow-tools "Bash(python3 *)"`. Without it, Bash calls are denied and show up in
  `permission_denials`.
- Pin a dated model snapshot for CI and baselines; use an alias (`sonnet`) for drift canaries.
  The resolved model id is recorded per trial either way.

Exit codes: 0 completed, 2 bad input, 3 aborted (budget cap or Ctrl-C).

## Lift (does the skill help at all?)

Run the same behavior tests twice, once with the skill removed, then hand both runs to skillcheck-compare:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/run_suite.py run suite.yaml --kind behavior adversarial --run-id with-skill
python3 ${CLAUDE_SKILL_DIR}/scripts/run_suite.py run suite.yaml --kind behavior adversarial --without-skill --run-id no-skill
```

## Resume

A crashed or interrupted run continues without redoing finished trials (it verifies the log first):
`python3 ${CLAUDE_SKILL_DIR}/scripts/run_suite.py resume .skillcheck/runs/<run-id>`

## Reading results

- Terminal summary: pass k/n with a 95% Wilson interval per test. Never call a test passed or failed from
  a single trial.
- Per-trial evidence lives in `.skillcheck/runs/<run-id>/trials/<test>/<n>/`: `trace.jsonl` (raw Claude Code
  stream-json, secrets redacted), `summary.json` (parsed tool calls, skills loaded, tokens, cost),
  `final_output.txt` and `files/` (what the agent produced).
- When a test fails, read the failing grader's `evidence` and that trial's `summary.json` before you guess at a cause.
- For verdicts, comparisons, gating and reports, use the **skillcheck-compare** skill.

## LLM judges

`llm_rubric` graders are excluded from gating until calibrated against human labels:
`python3 ${CLAUDE_SKILL_DIR}/scripts/run_suite.py calibrate-judge --rubric rubrics/x.md --labels labels.jsonl --judge sonnet`
(JSONL rows: `{"task": ..., "output": ..., "label": true|false}`, aim for 30+). It writes
`x.md.calibration.json` with Cohen's kappa. Editing the rubric invalidates the calibration.

## Safety and isolation

Read [isolation and safety](references/isolation.md) before running a skill you did not write. In short:
trials run on this machine in temp folders with web tools disabled and a limited tool set, but Bash can still
reach the network and filesystem. Use `--grader-sandbox docker` for Python graders, and run untrusted skills
inside a container or VM.

All options, the run folder layout and the results format are in [the runner reference](references/runner.md).
