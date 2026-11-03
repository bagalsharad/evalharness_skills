#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml>=5.4"]
# ///
"""Validate a skillcheck suite and show what it will run.

  validate_suite.py SUITE.yaml [--json]

Checks spec_version, required keys per test kind, grader fields, fixture paths,
`extends` and matrix expansion. Also reports coverage gaps (no near-miss
negatives, no adversarial tests, too few trials, unreviewed tests).
Python 3.9+; YAML suites need PyYAML (or run with `uv run`).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skillcheck_core as core  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("suite")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        suite = core.load_suite(Path(a.suite))
    except (core.SuiteError, ValueError, OSError) as e:
        print("invalid: %s" % e)
        return 2
    errors = core.validate_suite(suite)
    tests = core.expand_tests(suite) if not errors else []
    kinds = {}
    for t in tests:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    gaps = []
    if tests:
        trig = [t for t in tests if t["kind"] == "trigger"]
        selection_only = not trig and kinds.get("interaction")
        if selection_only:
            pass  # interaction suites measure selection; trigger coverage lives in per-skill suites
        elif not any(t["expect_trigger"] for t in trig):
            gaps.append("no should-trigger tests: recall is unmeasured")
        if not selection_only and not any(not t["expect_trigger"] for t in trig):
            gaps.append("no should-NOT-trigger tests: precision is unmeasured")
        if not selection_only and len([t for t in trig if not t["expect_trigger"]]) < 2:
            gaps.append("fewer than 2 negative trigger prompts; add near-misses that share keywords")
        if not kinds.get("behavior"):
            gaps.append("no behavior tests: skill lift cannot be measured")
        if selection_only and not any(t.get("expect_skill") is None for t in tests if t["kind"] == "interaction"):
            gaps.append("no interaction test with expect_skill: null (a prompt where no skill should load)")
        if not kinds.get("adversarial") and not selection_only:
            gaps.append("no adversarial tests (prompt injection in inputs, out-of-scope requests)")
        if not any(t.get("critical") for t in tests):
            gaps.append("no test marked critical: true (critical tests gate individually)")
        low = [t["id"] for t in tests if int(t["trials"]) < 5]
        if low:
            gaps.append("%d test(s) with < 5 trials; verdicts will mostly be inconclusive" % len(low))
        judged = [t["id"] for t in tests if any(g.get("type") == "llm_rubric" for g in t.get("graders") or [])]
        if judged:
            gaps.append("%d test(s) use an LLM judge; calibrate it (run_suite.py calibrate-judge) before gating" % len(judged))
        todo = [t["id"] for t in tests if "TODO" in t["prompt"]]
        if todo:
            errors.append("prompts still contain TODO: %s" % ", ".join(todo))
    unreviewed = [t["id"] for t in tests if not t["reviewed"]]
    result = {"suite": suite.get("suite"), "valid": not errors, "errors": errors, "tests": len(tests),
              "kinds": kinds, "trials_total": sum(int(t["trials"]) for t in tests),
              "unreviewed": unreviewed, "coverage_gaps": gaps}
    if a.json:
        print(json.dumps(result, indent=2))
    else:
        print("%s: %s" % (a.suite, "VALID" if not errors else "INVALID"))
        for e in errors:
            print("  error: " + e)
        if tests:
            print("  %d tests (%s), %d trials total" % (len(tests), ", ".join("%s=%d" % kv for kv in sorted(kinds.items())),
                                                       result["trials_total"]))
        if unreviewed:
            print("  %d unreviewed (not used for gating): %s" % (len(unreviewed), ", ".join(unreviewed[:12])))
        for g in gaps:
            print("  gap: " + g)
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
