# Contributing

Thanks for helping. Contributions are accepted under the Apache-2.0 license with a
[Developer Certificate of Origin](https://developercertificate.org/) sign-off (`git commit -s`).

## Setup

No install needed: Python 3.9+ and the standard library. PyYAML is optional (YAML suites).

```bash
python3 -m unittest discover -s tests -v   # offline; tests/fake/claude stands in for the Claude Code CLI
```

## Ground rules

- **Shared code goes in `core/skillcheck_core.py`**, then run `python3 tools/sync_core.py`. Each skill ships a
  copy; `tests/` fails if copies drift.
- **Stdlib only** in skill scripts. Optional imports (PyYAML, jsonschema) must degrade gracefully.
- **Statistics changes need reference values** (cross-check against SciPy/statsmodels and pin the numbers in a test)
  and must keep the A/A simulation test at or below alpha.
- **New CLI behaviour that touches Claude Code** must be checked against a real trace. Add a sanitized one under
  `tests/data/` (strip paths, usernames and session ids).
- Keep every `SKILL.md` lint-clean: `python3 skills/skillcheck-lint/scripts/lint_skill.py --library skills --strict`.
- Update `CHANGELOG.md`.

## Adding a grader

1. Add the type to `GRADER_TYPES` (and required fields in `validate_suite`) in the core.
2. Implement it in `run_graders()` in `skills/skillcheck-run/scripts/run_suite.py`. Return `_grade(type, passed, evidence, score)`.
   Never raise: grader errors become failed grades.
3. Document it in `skills/skillcheck-suite/references/spec-v1.md` and `suite.schema.json`.
4. Add a unit test and, if it uses the trace, an end-to-end test with the fake CLI.

## Adding a runtime

The runner calls one function per trial (`run_one_trial`) and parses one trace format (`parse_trace`). A new runtime
needs a command builder and a parser that produce the same summary fields (`skills_loaded`, `tool_calls`,
`metrics`, `final_text`, `result`). Open an issue first to agree on the interface.
