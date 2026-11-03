---
name: skillcheck-lint
description: Lints and audits Agent Skill folders - SKILL.md frontmatter rules, naming, description quality, broken file references, scripts with no run instructions, leaked secrets and oversized bodies - and reports each skill's content hash and context token cost. Also finds skills in a library whose descriptions overlap enough to steal each other's prompts. Use when the user asks to lint, validate, check or audit a skill or a skills folder, or before publishing or testing a skill.
license: Apache-2.0
compatibility: Needs Python 3.9+. PyYAML optional.
---

# skillcheck-lint

Static checks run before any model call. They are free and take seconds.

## Run

One or more skills:
`python3 ${CLAUDE_SKILL_DIR}/scripts/lint_skill.py path/to/skill [path/to/other-skill ...]`

A whole library, including description-collision checks:
`python3 ${CLAUDE_SKILL_DIR}/scripts/lint_skill.py --library ~/.claude/skills`

Options: `--json` (machine-readable), `--strict` (warnings fail too, for CI), `--min-overlap 0.35`
(collision sensitivity). Exit code 1 means there are findings at the failing level.

## What it checks

Rules follow the Agent Skills spec (agentskills.io/specification) and the Claude Code skills docs:

- **Errors**: missing or invalid frontmatter; `name` not lowercase-hyphenated, over 64 chars, or different from
  the folder name; empty or over-1024-char `description`; `compatibility` over 500 chars; broken relative
  links; references that escape the skill folder; reserved folder names; likely secrets or private keys.
- **Warnings**: description too short, or never says *when* to use the skill; body over 500 lines or ~5000
  tokens; scripts with no shebang and no run instruction in SKILL.md; frontmatter keys Claude Code ignores;
  description + `when_to_use` over the 1536-char listing limit; files over 5 MB.
- **Info**: Claude Code-only keys (rejected by claude.ai uploads and the Skills API); deep reference chains.

It also prints:
- the **content hash**: a canonical hash of the folder (sorted paths, normalized line endings). This is the
  skill's version identity in skillcheck runs. Git SHA and dirty flag are printed when available.
- the **context footprint**: estimated tokens for metadata (always in context), body (loaded on trigger)
  and referenced files (loaded on demand). Estimates use ~4 characters per token.

## After linting

- Fix errors before running any tests. Offer to fix them for the user, and keep the skill's intent unchanged.
- When a description is vague, suggest a rewrite that states what the skill does and when to use it, with
  the words users would actually type. Triggering depends on it.
- For each **collision** pair, recommend interaction tests (skillcheck-suite skill) and sharper, contrasting
  descriptions. Lint finds candidate collisions from word overlap; only an interaction test shows whether the
  model actually confuses the two skills.
