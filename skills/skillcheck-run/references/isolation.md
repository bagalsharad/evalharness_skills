# Isolation and safety

Treat every skill under test as untrusted code and every model output as untrusted input.
This page says what the runner isolates, what it does not, and how to tighten it.

## What each trial gets

| Control | Default | Notes |
|---|---|---|
| Working directory | fresh `mkdtemp` folder, `git init`-ed | Outside your repo, so parent `.claude/skills`, `CLAUDE.md` and settings are not picked up. Deleted after grading (`--keep-workdirs` keeps it). |
| Skills | copies of the skills under test in `<work>/.claude/skills/` | Copies, so a skill that edits itself cannot change your source. |
| Fixtures | copied in; paths cannot escape the workdir | |
| Settings | `--setting-sources project` | Your user settings, hooks and permissions are not loaded. |
| Tools | `Bash,Read,Write,Edit,Skill,TodoWrite` | `--tools` to change. WebFetch and WebSearch are always disallowed. |
| Permissions | `acceptEdits` plus pre-approved `Read Write Edit Skill TodoWrite` | Bash is **not** pre-approved; in headless mode unapproved calls are denied and recorded in `permission_denials`. Opt in per suite or test with `allow_tools: ["Bash(python3 *)"]`. |
| Session | `--no-session-persistence` | Trials do not show up in your session history. |
| Cost | `--max-budget-usd` per trial (default $1, or 2x a test's `budget.max_cost_usd`), run cap `--max-run-usd` (default $10) | When the run cap is reached, no new trials start. |
| Time | per-test `timeout_s`; the process is killed after it | A timeout counts as a failed trial, not an error. |
| Environment | `TZ=UTC`, `LANG=en_US.UTF-8`, `SKILLCHECK_TRIAL=1` | Your other environment variables are passed through (Claude Code needs its auth). |
| Secrets in logs | traces and stderr pass through a redaction filter | Covers common key shapes (Anthropic, OpenAI, AWS, GitHub, Slack, bearer tokens). It is not exhaustive. |

## What is NOT isolated

- **Bash runs on your machine.** If a test pre-approves Bash, the agent (following the skill) can read your
  files and reach the network. `Bash(python3 *)` limits the command prefix, not what that Python does.
- **Built-in and claude.ai-synced skills stay visible.** `--setting-sources project` does not hide bundled
  skills. Each trial records the `visible_skills` list, and skillcheck-compare warns when a skill outside the
  suite was loaded. For a clean slate, use `--clean-config` (throwaway `CLAUDE_CONFIG_DIR`; needs
  `ANTHROPIC_API_KEY` because OAuth logins live in your normal config), or `--bare` (also needs an API key).
- **Python and command graders run on the host by default.** They are your code, but they read files the agent
  wrote. Use `--grader-sandbox docker` to run them in `python:3.12-slim` with `--network none`, a read-only
  root, read-only mounts, all capabilities dropped, and memory and pid limits.
- **Model nondeterminism.** Nothing here makes a model deterministic. That is why every test runs several
  trials and verdicts come with confidence intervals.

## Recommended setups

| Situation | Setup |
|---|---|
| Your own skill, local dev loop | defaults |
| Someone else's skill, or a skill that runs scripts | run the whole harness in a dev container or VM with no credentials except a scoped API key; add `--grader-sandbox docker` |
| CI | ephemeral runner, scoped API key from the CI secret store (OIDC where your provider supports it), `--clean-config`, pinned model snapshot |

## Threats considered (STRIDE summary)

| Threat | Example | Mitigation here | Residual risk |
|---|---|---|---|
| Spoofing | skill impersonates another skill's name | interaction tests; lint flags duplicate names and similar descriptions | low |
| Tampering | someone edits results to pass a gate | hash-chained `events.jsonl`; `analyze.py verify`; baselines refuse runs that fail verification | an attacker who rewrites the whole chain; sign the head hash to cover that |
| Repudiation | "that version passed" disputes | manifest records skill content hashes, git SHAs, suite hash, CLI version and resolved model ids | — |
| Information disclosure | skill exfiltrates `~/.ssh` or env keys | Bash not pre-approved; web tools off; redaction | high when Bash is allowed; use a container |
| Denial of service | fork bomb, infinite loop, runaway cost | timeouts, per-trial and per-run cost caps; Docker pid and memory limits for graders | agent-side Bash fork bomb on host; use a container |
| Elevation of privilege | prompt injection in fixtures drives the agent | adversarial test kind; judge prompt marks agent output as untrusted data | inherent to LLM agents; test for it |
