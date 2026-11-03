---
name: skillcheck
description: Guides the end-to-end workflow for testing Agent Skills with skillcheck - lint the skill, write a test suite, run trials in Claude Code, then get statistically sound verdicts, gates and reports. Use when the user wants to start testing a skill, asks how to know whether a skill works or regressed, or wants the whole lint, suite, run and compare loop in one go.
license: Apache-2.0
compatibility: Designed for Claude Code. Needs Python 3.9+ and the claude CLI.
---

# skillcheck

skillcheck is a regression-testing harness for Agent Skills. It catches three silent failures:
1. a **skill edit** breaks behavior that used to work;
2. a **model change** alters how the skill is triggered or followed;
3. a **new skill** steals prompts from, or changes the behavior of, an existing one.

It is four skills used in order. Hand off to each one; its SKILL.md has the commands.

| Step | Skill | Output |
|---|---|---|
| 1. Static checks (free, seconds) | **skillcheck-lint** | findings, content hash, token footprint, description collisions |
| 2. Write tests | **skillcheck-suite** | `skillcheck-tests/<skill>/suite.yaml` + fixtures + graders |
| 3. Run trials | **skillcheck-run** | `.skillcheck/runs/<run-id>/` (hash-chained log, traces, results.json) |
| 4. Decide | **skillcheck-compare** | verdicts with confidence intervals, gate exit code, HTML/MD/JUnit reports |

## Typical flows

**First time on a skill**
lint → scaffold a suite and fill it in (the user reviews it) → `run --dry-run` to show the cost → a small
smoke run (`--tags smoke --trials 3`) → fix obvious test bugs → a full run → `analyze.py summary` →
`analyze.py baseline save main RUN`.

**After editing a skill**
lint → run the same suite → `analyze.py compare baseline:main NEW_RUN` → explain the verdicts → when the change is
intended and good, re-pin the baseline.

**Is the skill worth having?** Run behavior tests with and without the skill, then `analyze.py compare --lift`.

**Model upgrade or drift:** run the suite on the new model (or alias) and compare against the pinned-model
baseline; schedule it nightly with the drift command (see skillcheck-compare).

**Library with several skills:** `lint_skill.py --library DIR` → interaction suite for flagged pairs.

## Ground rules

- Never report pass or fail from one trial. Use at least 5 trials, and report rates with intervals.
- Always show the user the dry-run cost estimate before a paid run and keep `--max-run-usd` set.
- Suites live outside the skill folder. Generated tests stay `reviewed: false` until the user approves them.
- Treat skills under test as untrusted: read `skillcheck-run/references/isolation.md` before running someone
  else's skill.
- If the other skillcheck-* skills are missing, tell the user to install the whole skillcheck pack.
