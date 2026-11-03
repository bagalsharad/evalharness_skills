# skillcheck suite format, spec_version 1

A suite is YAML (or JSON). Paths are relative to the suite file. A JSON Schema is in
[suite.schema.json](suite.schema.json).

```yaml
spec_version: 1                 # required, must be 1
suite: my-skill                 # required: suite name
description: optional text
extends: ../base/suite.yaml     # optional: inherit defaults + tests (child tests replace parent tests by id)
skills: [../../skills/my-skill] # skills under test (the first is the default for trigger tests)
distractors: [../other-skill]   # installed only for kind: interaction tests
defaults:                       # applied to every test unless the test sets it
  trials: 5
  timeout_s: 300
  model: claude-sonnet-4-5      # optional
  allow_tools: ["Bash(python3 *)"]
  tools: "Bash,Read,Write,Edit,Skill,TodoWrite"
  budget: {max_cost_usd: 0.5}
tests: [...]
```

## Test fields

| Field | Kinds | Meaning |
|---|---|---|
| `id` | all | Required, unique. Letters, digits, `_ . -`. |
| `kind` | all | `trigger`, `behavior`, `adversarial`, `interaction`, `budget`. |
| `prompt` | all | Required. The user message. Supports `{{var}}` with `matrix`. |
| `tags` | all | For `--tags` filtering (`smoke`, `security`...). |
| `trials`, `timeout_s`, `model` | all | Override defaults. |
| `fixtures` | all | List of paths, or `{src, dest}` maps. Copied into the trial workdir. Files and folders. |
| `setup` | all | Shell commands run in the workdir before the agent starts (trusted suite code). |
| `graders` | all | List; all must pass. Required for behavior and adversarial. |
| `budget` | all | `max_cost_usd`, `max_total_tokens`, `max_output_tokens`, `max_tool_calls`, `max_turns`, `max_duration_s`. Required for kind budget; adds a budget grade to any other kind. |
| `expect_trigger` | trigger | Required bool. |
| `skill` | trigger | Which skill to check (default: first skill under test). |
| `expect_skill` | interaction | Required: skill name, or `null` for "no skill should load". |
| `exclusive` | interaction | Default true: no other installed skill may load. |
| `skills` | all | Override the installed skill set for this test (names from `skills`/`distractors`, or paths). |
| `allow_tools`, `tools` | all | Extra pre-approved tools / built-in tool list. |
| `critical` | all | Gated individually (its own FDR family). |
| `quarantine`, `quarantine_reason` | all | Runs and is reported, never gated. For known-flaky tests. |
| `reviewed` | all | Default true. Generated tests use `false` until a human reviews them; unreviewed tests do not gate. |
| `matrix` | all | Map of lists (cartesian product) or list of maps. Expands to `id[0]`, `id[1]`... |
| `notes`, `description` | all | Free text. |

## Graders

Order of preference: deterministic, then trace, then LLM judge.

| type | Fields | Passes when |
|---|---|---|
| `file_exists` | `path` | the file exists in the workdir |
| `file_absent` | `path` | it does not |
| `file_contains` | `path`, `text` or `pattern` (regex, multiline), `negate` | content matches |
| `file_hash` | `path`, `sha256` | exact bytes match |
| `output_contains` | `text`, `ignore_case`, `negate` | the agent's final reply contains text |
| `output_regex` | `pattern`, `ignore_case`, `negate` | final reply matches (dotall + multiline) |
| `json_schema` | `path`, `schema` (inline or file path) | file is valid JSON matching the schema (uses `jsonschema` if installed, else a built-in subset) |
| `python` | `entrypoint`, `args`, `timeout_s` | script exits 0, or prints `{"pass": true, ...}` as its last line |
| `command` | `run` (shell), `timeout_s` | same as python |
| `trace` | see below | the trace satisfies every listed rule |
| `budget` | same keys as the `budget` block | within limits |
| `llm_rubric` | `rubric` (file), `judge` (model or list for majority vote), `pass_threshold` (0.7), `include_files` (globs) | judge score >= threshold (majority when several judges) |

`trace` rules: `must_load_skill`, `must_not_load_skill` (name or list), `max_tool_calls`, `min_tool_calls`,
`forbidden_tools`, `required_tools`, `tool_order` (subsequence), `must_read` (path fragments that must be
Read or cat-ed, e.g. `references/format.md`), `max_turns`, `no_permission_denials: true`,
`count_skill_md_reads` (default true).

Tool patterns: `Write` matches the tool name; `Bash(git *)` matches the Bash command with a glob; `Write(*.json)`
matches the file path.

## Fixtures

```yaml
fixtures:
  - fixtures/data.csv                    # copied as data.csv
  - {src: fixtures/poison.txt, dest: notes.txt}
  - {src: fixtures/project, dest: .}     # copy a folder's contents
```

## Example

See `examples/suites/` in the skillcheck repo: `haiku-poet` (formatting skill), `csv-summary`
(script-backed skill) and `interaction` (several skills competing).
