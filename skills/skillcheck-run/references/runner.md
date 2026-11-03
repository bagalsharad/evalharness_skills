# Runner reference

`run_suite.py` subcommands: `run`, `resume`, `validate`, `doctor [--live]`, `calibrate-judge`.

## `run` options

| Option | Default | Meaning |
|---|---|---|
| `--skill PATH` (repeat) | suite `skills:` | Skills under test. The first one is the default for trigger tests. |
| `--distractor PATH` (repeat) | suite `distractors:` | Extra skills installed only for `interaction` tests. |
| `--model` | Claude Code default | Alias (`sonnet`) or full id. Pin a dated snapshot for CI. |
| `--trials N` | per test (suite default 5) | Overrides every test. |
| `--tags`, `--exclude-tags`, `--test`, `--kind` | all | Filters. `--test` matches matrix parents too (`--test trigger-paraphrase`). |
| `--concurrency` | 3 | Parallel trials. Raise carefully; rate limits are retried with backoff. |
| `--max-run-usd` | 10 | Stop starting trials once spend reaches this (0 = no cap). |
| `--max-trial-usd` | 1.0 | Passed to `claude --max-budget-usd`. |
| `--est-cost-per-trial` | 0.15 | Used by `--dry-run`. Calibrate from a previous run's `totals.cost_usd / trials`. |
| `--retries` | 2 | Retries for infrastructure errors only (no result event, API error status). Exponential backoff with jitter. Failed trials are never retried. |
| `--without-skill [NAME ...]` | — | Remove skills (no names = all skills under test) for lift baselines. |
| `--setting-sources` | `project` | Passed to Claude Code. |
| `--permission-mode` | `acceptEdits` | Passed to Claude Code. |
| `--tools` | `Bash,Read,Write,Edit,Skill,TodoWrite` | Built-in tools available in trials. |
| `--allow-tools` | — | Extra pre-approved tools, e.g. `"Bash(python3 *)"`. |
| `--grader-sandbox` | `none` | `docker` runs python/command graders in a locked-down container. |
| `--clean-config` | off | Throwaway `CLAUDE_CONFIG_DIR` (needs `ANTHROPIC_API_KEY`). |
| `--bare` | off | Pass `--bare` to Claude Code (needs `ANTHROPIC_API_KEY`). |
| `--extra-arg X` (repeat) | — | Raw extra arguments for `claude`. |
| `--claude-bin` | `$SKILLCHECK_CLAUDE_BIN`, `claude` on PATH, `~/.local/bin/claude` | CLI to run. |
| `--keep-workdirs` | off | Keep temp project folders for debugging. |
| `--dry-run` | — | Print tests, trial count and cost estimate; run nothing. |

## Run folder

```
.skillcheck/runs/<run-id>/
  manifest.json      inputs: suite hash, skill content hashes + git SHA, target, argv, versions
  events.jsonl       append-only, hash-chained log (source of truth)
  results.json       derived from events.jsonl (regenerated on resume)
  trials/<test>/<n>/
    trace.jsonl      raw Claude Code stream-json (redacted)
    summary.json     parsed trace: tool calls, skills loaded, files written, tokens, cost
    final_output.txt the agent's last reply
    stderr.txt
    files/           files the agent left in the workdir (20 MB cap per trial)
```

## Event log and tamper evidence

Each line in `events.jsonl` has `prev_hash` and `hash = sha256(prev_hash + canonical_json(record))`.
`run_started` stores the manifest hash; `results.json` stores the final `chain_head`.
`resume` refuses a log that fails verification. `analyze.py verify RUN` checks all of it.
To make results attestable, sign `chain_head` (for example with `cosign sign-blob`) and store the signature
next to the run.

## Trial statuses

| Status | Meaning | Counts in pass rate? |
|---|---|---|
| `passed` | every grader passed | yes (pass) |
| `failed` | at least one grader failed | yes (fail) |
| `timeout` | killed at `timeout_s`; graded on what exists, plus a failing `timeout` grade | yes (fail) |
| `error` | infrastructure problem after retries (no result event, API error) | no; reported separately |

## How triggering is detected

From the trace: an assistant `tool_use` named `Skill` with `input.skill == <name>` (plugin skills appear as
`plugin:name`). As a fallback, a `Read` of `.../skills/<name>/SKILL.md` also counts, because some agents read
the file directly. Verified against Claude Code 2.1.x stream-json; `tests/data/` holds a real trace.

## results.json (schema_version 1)

Top level: `run_id, status, created_at, finalized_at, suite{name,path,sha256,spec_version}, skills[],
distractors[], target{runtime, runtime_version, model, model_is_alias, ...}, config, runner,
observed_models[], observed_runtime_versions[], model_changed_mid_run, totals{cost_usd,tests,trials,errors},
tests[], chain_head`.

Each test: `id, kind, tags, critical, quarantine, reviewed, prompt, passed, graded, errors, trials[]`.
Each trial: `trial, status, passed, score, grades[], metrics{cost_usd, duration_ms, num_turns, tool_calls,
input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, total_tokens, wall_ms},
skills_loaded, installed_skills, visible_skills, model, runtime_version, permission_denials,
terminal_reason, final_text, files[], trial_dir, attempts`.
Each grade: `type, passed, score (0..1), evidence, grader_version` plus type-specific fields.

## Grader environment (python / command)

cwd = trial workdir. Env: `SKILLCHECK_WORK`, `SKILLCHECK_SUITE_DIR`, `SKILLCHECK_OUTPUT` (final reply file),
`SKILLCHECK_TRACE_SUMMARY` (parsed trace JSON). Print one JSON line `{"pass": bool, "score": 0..1, "evidence": "..."}`
as the last line of stdout, or just use the exit code (0 = pass).
