# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning: semver for the scripts, `spec_version` for suites, `schema_version` for results.

## [0.1.0] - 2026-11-02
### Added
- Five Claude Code skills: `skillcheck`, `skillcheck-lint`, `skillcheck-suite`, `skillcheck-run`, `skillcheck-compare`.
- Suite format spec v1 with JSON Schema; five test kinds; 12 grader types including calibrated LLM judges.
- Runner on Claude Code headless mode with resumable, hash-chained run logs and cost caps.
- Statistics: Wilson, Newcombe, Fisher exact, exact McNemar/sign test, pass@k, pass^k, BH FDR, paired bootstrap, power/MDE.
- Baselines, lift, CI gate policy, drift alerts with de-duplication, flaky/shift detection, HTML/Markdown/JUnit reports.
- Mutation tool for seeded-regression testing of suites; three worked examples; GitHub Actions workflow.
