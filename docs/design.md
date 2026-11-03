# skillcheck design notes

This repo implements the local-first part of the "skillcheck Master Build Prompt" as a pack of Claude Code skills.
This page records the decisions made, the assumptions behind them, and what from the brief is not built yet.

## Decisions (ADR summaries)

| # | Decision | Why | Revisit when |
|---|---|---|---|
| 001 | Ship as **Claude Code skills** (plus an optional plugin manifest), not a pip package | The request was for skills people run locally; skills install by copying a folder and Claude can drive the workflow | a hosted or team service is needed |
| 002 | **Stdlib-only Python 3.9+**, PyYAML optional (PEP 723 headers for `uv run`) | Works on stock macOS and Linux Python; no dependency install step | statsmodels or Pydantic is worth the install cost |
| 003 | **Vendored core library**: `core/skillcheck_core.py` is copied into each skill by `tools/sync_core.py`; a test enforces identical copies | Each skill must work on its own after install; skills cannot import from each other | Claude Code offers shared skill libraries |
| 004 | **Runtime = Claude Code headless** (`claude -p --output-format stream-json`), one subprocess per trial | It is the real skills runtime, and trace format verified against 2.1.224/2.1.316 | other runtimes (Agent SDK, other agents) need adapters |
| 005 | **Skill activation from the trace**: a `Skill` tool_use, with a `Read` of `skills/<name>/SKILL.md` as fallback | Verified with a live trace (`tests/data/real_trace_cc2.1.224.jsonl`) | the event format changes |
| 006 | **Isolation = temp project + restricted tools/permissions**, Docker only for graders | Running Claude Code itself in a container needs auth plumbing that most local users do not have | an official sandboxed runtime image exists |
| 007 | **Event-sourced runs**: append-only hash-chained JSONL; results derived from it; resume from it | Reproducible, resumable, tamper-evident without a database | team storage (Postgres/S3) is needed |
| 008 | **Per-test comparisons are unpaired** (Fisher + Newcombe); pairing happens across tests (bootstrap + sign test) | No seed control means trial i on each side is not a real pair | providers offer reproducible seeding |
| 009 | **BH FDR within families** (critical, gated, informational) | Non-gating tests should not dilute power; the brief asks for critical tests to be gated separately | — |
| 010 | **Untrusted by default**: generated tests unreviewed, uncalibrated judges non-gating, infra errors never counted as failures | Optimizes for correct claims over convenience | — |
| 011 | License Apache-2.0; DCO for contributions (no CLA) | Low friction for contributors | — |

## Assumptions (flagged for the owner)

1. **Local single-user use is the v1 target.** No Postgres, S3, RBAC or multi-tenancy.
2. **Claude Code is the only runtime adapter.** Other model providers (OpenAI-compatible, Bedrock, Ollama) are
   out of scope, because skills are a Claude Code feature here. Bedrock and Vertex work through Claude Code's own
   provider settings.
3. **Token counts in lint are estimates** (~4 chars per token). Run-time token and cost numbers come from Claude Code.
4. **Judges are Claude models.** Different model *families* (as the brief suggests) would need a non-Claude judge
   adapter. Mitigations used instead: a different tier, majority vote, calibration against human labels.
5. **Default trials = 5.** This is the floor for any verdict; the power tooling shows why critical tests need more.

## Brief coverage

| Brief section | Status |
|---|---|
| 5.1 Ingestion: frontmatter, lint, canonical hash, git SHA/dirty, footprint | done |
| 5.2 Spec: 5 test kinds, JSON Schema, inheritance, matrix, tags, quarantine, `init`, reviewed flag | done (`gen` is Claude-driven via the suite skill, not a separate LLM command) |
| 5.3 Engine: async bounded concurrency, backoff + jitter, resumable, trace capture, budget caps, dry-run estimate | done; **gaps**: per-provider rate limiter, response cache, OpenTelemetry spans |
| 5.3 Sandbox: Docker per trial | **partial**: Docker for graders only; trials isolated by temp project + permissions |
| 5.4 Graders: deterministic, trace, LLM judge with calibration (kappa), human-label loop | done; **gap**: human review-queue export UI (calibration consumes JSONL labels) |
| 5.5 Stats: Wilson, pass@k, pass^k, paired comparisons, BH, MDE/power, flake detection, 4 verdicts | done; **gap**: adaptive sequential testing (documented, not implemented) |
| 5.6 Baselines, lift, drift with alert state machine, interaction testing | done; **gap**: automated bisect across commits and snapshots |
| 5.7 Reporting: JSON, JUnit, HTML with trace viewer, Markdown PR comment, webhook/Slack | done; **gaps**: SARIF, Parquet, OTel export, PagerDuty-native payload |
| 6 Architecture: hexagonal, event-sourced, tamper-evident | event-sourced and hash-chained; no plugin entry points (single runtime) |
| 8 Deployment: GitHub Action, gate policy, drift schedule, runbooks | workflow + gate policy + runbook; **gaps**: Helm, Terraform, signed releases, SBOM |
| 9 Meta-evaluation: reference tests, A/A, seeded regressions, judge validation | reference values + A/A and power simulations in tests; mutation tool shipped; **gap**: a published detection-power table from live runs |

## Open questions from the brief, answered

- **Which runtime exposes reliable skill-load events?** Claude Code's stream-json: `Skill` tool_use events.
  Fallback: SKILL.md reads. The init event's `skills` list also records what was visible.
- **Provider nondeterminism and minimum trials?** It cannot be controlled, so it is measured: at least 5
  trials for a verdict, and `analyze.py power` sizes critical tests.
- **Judge independence?** Calibration against human labels (kappa ≥ 0.6) is required for gating; majority vote
  of several judges is supported; a judge on the same model as the target is flagged.
- **Skills needing network or credentials?** Fixtures and recorded data are preferred. Web tools are off by default;
  a test can pre-approve specific Bash commands. Real credentials should not go into trials.
- **Model alias floating mid-run?** The resolved model id is recorded per trial; `model_changed_mid_run` is
  flagged in results and warned about in summaries and gates.
