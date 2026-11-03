"""skillcheck test suite (stdlib unittest; no network, no API calls).

Run:  python3 -m unittest discover -s tests -v
End-to-end tests use tests/fake/claude, a stand-in for the Claude Code CLI that
emits the same stream-json shapes as Claude Code 2.1.x.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "skills" / "skillcheck-run" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "skillcheck-compare" / "scripts"))
import skillcheck_core as core  # noqa: E402
import run_suite  # noqa: E402
import analyze  # noqa: E402

PY = sys.executable
RUN = ROOT / "skills" / "skillcheck-run" / "scripts" / "run_suite.py"
ANALYZE = ROOT / "skills" / "skillcheck-compare" / "scripts" / "analyze.py"
FAKE = ROOT / "tests" / "fake" / "claude"
DATA = ROOT / "tests" / "data"


class StatsReferenceValues(unittest.TestCase):
    """Reference values cross-checked against scipy 1.x / statsmodels 0.14 (see docs/statistics.md)."""

    def test_wilson(self):
        lo, hi = core.wilson_interval(8, 10)
        self.assertAlmostEqual(lo, 0.4901624, places=6)
        self.assertAlmostEqual(hi, 0.9433178, places=6)
        self.assertEqual(core.wilson_interval(0, 0), (0.0, 1.0))
        lo, hi = core.wilson_interval(10, 10)
        self.assertAlmostEqual(lo, 0.7224672, places=6)
        self.assertEqual(hi, 1.0)

    def test_fisher(self):
        # scipy.stats.fisher_exact([[10,0],[5,5]]) -> 0.03250774
        self.assertAlmostEqual(core.fisher_exact_two_sided(10, 10, 5, 10), 0.0325077, places=6)
        # [[6,0],[1,5]] -> 0.01515152
        self.assertAlmostEqual(core.fisher_exact_two_sided(6, 6, 1, 6), 0.0151515, places=6)
        self.assertAlmostEqual(core.fisher_exact_two_sided(5, 10, 5, 10), 1.0, places=9)

    def test_binom_mcnemar(self):
        # scipy.stats.binomtest(0, 6, 0.5).pvalue -> 0.03125
        self.assertAlmostEqual(core.mcnemar_exact(0, 6), 0.03125, places=9)
        self.assertAlmostEqual(core.mcnemar_exact(3, 3), 1.0, places=9)
        # scipy.stats.binomtest(2, 10, 0.3).pvalue -> 0.733172068
        self.assertAlmostEqual(core.binom_test_two_sided(2, 10, 0.3), 0.7331721, places=6)

    def test_newcombe(self):
        # statsmodels confint_proportions_2indep(5,10,10,10, method='newcomb') for p2-p1 with p1=10/10
        lo, hi = core.newcombe_diff_interval(10, 10, 5, 10)
        self.assertAlmostEqual(lo, -0.7634069, places=6)
        self.assertAlmostEqual(hi, -0.1173675, places=6)

    def test_bh(self):
        # statsmodels multipletests([.01,.04,.03,.2], method='fdr_bh')
        q = core.benjamini_hochberg([0.01, 0.04, 0.03, 0.2])
        for got, want in zip(q, [0.04, 0.05333333, 0.05333333, 0.2]):
            self.assertAlmostEqual(got, want, places=6)

    def test_pass_at_k(self):
        self.assertAlmostEqual(core.pass_at_k(10, 3, 1), 0.3)
        self.assertAlmostEqual(core.pass_at_k(10, 3, 5), 1 - 21 / 252)
        self.assertAlmostEqual(core.pass_hat_k(10, 8, 3), 56 / 120)
        self.assertEqual(core.pass_hat_k(10, 2, 3), 0.0)
        self.assertEqual(core.pass_at_k(10, 8, 3), 1.0)
        self.assertIsNone(core.pass_at_k(2, 1, 3))

    def test_kappa(self):
        self.assertAlmostEqual(core.cohens_kappa([1, 1, 0, 0], [1, 1, 0, 0]), 1.0)
        self.assertAlmostEqual(core.cohens_kappa([1, 0, 1, 0], [1, 1, 0, 0]), 0.0)

    def test_bootstrap(self):
        m, lo, hi = core.paired_bootstrap_ci([0.0] * 10)
        self.assertEqual((m, lo, hi), (0.0, 0.0, 0.0))
        self.assertEqual(core.paired_bootstrap_pvalue([0.0] * 10), 1.0)
        m, lo, hi = core.paired_bootstrap_ci([-0.5] * 8 + [-0.4] * 8)
        self.assertLess(hi, 0)

    def test_mde_monotone(self):
        a = core.min_detectable_effect(0.9, 10, 10)
        b = core.min_detectable_effect(0.9, 40, 40)
        self.assertGreater(a, b)
        n = core.trials_needed(0.9, 0.2)
        self.assertLessEqual(core.min_detectable_effect(0.9, n, n), 0.2)

    def test_verdicts(self):
        self.assertEqual(core.decide_verdict(-0.5, 0.01, 0.3, 0.05, 0.2), "regression")
        self.assertEqual(core.decide_verdict(0.5, 0.01, 0.3, 0.05, 0.2), "improvement")
        self.assertEqual(core.decide_verdict(0.0, 0.9, 0.5, 0.05, 0.2), "inconclusive")
        self.assertEqual(core.decide_verdict(0.0, 0.9, 0.1, 0.05, 0.2), "no_significant_change")


class StatsCalibration(unittest.TestCase):
    """Meta-evaluation by simulation: A/A false-positive rate and detection power."""

    def test_aa_false_positive_rate_at_most_alpha(self):
        rng = random.Random(42)
        fp, sims = 0, 3000
        for _ in range(sims):
            p = rng.choice([0.5, 0.7, 0.9])
            k1 = sum(rng.random() < p for _ in range(10))
            k2 = sum(rng.random() < p for _ in range(10))
            r = core.compare_binary(k1, 10, k2, 10)
            if core.decide_verdict(r["effect"], r["p_value"], r["mde"], 0.05, 0.2, r["ci"]) == "regression":
                fp += 1
        self.assertLessEqual(fp / sims, 0.05)

    def test_large_drop_is_detected(self):
        rng = random.Random(7)
        hits = 0
        for _ in range(500):
            k1 = sum(rng.random() < 0.95 for _ in range(20))
            k2 = sum(rng.random() < 0.45 for _ in range(20))
            r = core.compare_binary(k1, 20, k2, 20)
            hits += core.decide_verdict(r["effect"], r["p_value"], r["mde"], 0.05, 0.2) == "regression"
        self.assertGreater(hits / 500, 0.9)


class Lint(unittest.TestCase):
    def make(self, name, text, extra=None):
        d = Path(tempfile.mkdtemp()) / name
        d.mkdir()
        (d / "SKILL.md").write_text(text)
        for rel, body in (extra or {}).items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(body)
        return core.load_skill(d)

    def codes(self, sk):
        return {f["code"] for f in core.lint_skill(sk)}

    def test_clean(self):
        sk = self.make("good-skill", "---\nname: good-skill\ndescription: Does a specific useful thing to files. Use when the user asks for that thing.\n---\n# Body\nSee [ref](references/a.md).\n",
                       {"references/a.md": "x"})
        self.assertEqual([f for f in core.lint_skill(sk) if f["level"] != "info"], [])

    def test_errors(self):
        sk = self.make("Bad--Name", "---\nname: Bad--Name\ndescription: short\n---\nSee [x](references/missing.md) and run scripts/run.py\n",
                       {"scripts/run.py": "if __name__ == '__main__':\n    print(1)\n",
                        "scripts/helpers.py": "def f():\n    return 1\n"})
        findings = core.lint_skill(sk)
        c = {f["code"] for f in findings}
        for code in ("SC004", "SC008", "SC009", "SC019", "SC022"):
            self.assertIn(code, c)
        # importable modules (no __main__ guard) are not entry points
        self.assertFalse(any("helpers.py" in f["message"] for f in findings))

    def test_name_mismatch_and_missing_frontmatter(self):
        self.assertIn("SC005", self.codes(self.make("folder", "---\nname: other\ndescription: Does x. Use when y is needed by the user.\n---\nbody")))
        self.assertIn("SC001", self.codes(self.make("nofm", "# no frontmatter")))

    def test_secret_detection(self):
        sk = self.make("leaky", "---\nname: leaky\ndescription: Does x. Use when y is needed by the user here.\n---\nkey sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAA\n")
        self.assertIn("SC025", self.codes(sk))

    def test_hash_is_line_ending_independent(self):
        a = self.make("h", "---\nname: h\ndescription: d\n---\nline1\nline2\n")
        b = self.make("h", "---\r\nname: h\r\ndescription: d\r\n---\r\nline1\r\nline2\r\n")
        self.assertEqual(a["content_hash"], b["content_hash"])

    def test_mini_yaml_fallback(self):
        fm = core._mini_yaml("name: x\ndescription: >\n  folded\n  text\nmetadata:\n  version: \"1\"\n")
        self.assertEqual(fm, {"name": "x", "description": "folded text", "metadata": {"version": "1"}})


class Suites(unittest.TestCase):
    def test_examples_valid(self):
        for p in (ROOT / "examples" / "suites").glob("*/suite.yaml"):
            s = core.load_suite(p)
            self.assertEqual(core.validate_suite(s), [], p)

    def test_matrix_and_extends(self):
        d = Path(tempfile.mkdtemp())
        (d / "base.yaml").write_text("spec_version: 1\nsuite: b\ndefaults: {trials: 3}\ntests:\n"
                                     "  - {id: a, kind: trigger, expect_trigger: true, prompt: hi}\n"
                                     "  - {id: b, kind: trigger, expect_trigger: false, prompt: yo}\n")
        (d / "child.yaml").write_text("spec_version: 1\nsuite: c\nextends: base.yaml\ndefaults: {timeout_s: 9}\ntests:\n"
                                      "  - id: b\n    kind: trigger\n    expect_trigger: true\n    prompt: 'say {{w}}'\n"
                                      "    matrix: {w: [x, y]}\n")
        s = core.load_suite(d / "child.yaml")
        self.assertEqual(core.validate_suite(s), [])
        tests = core.expand_tests(s)
        self.assertEqual([t["id"] for t in tests], ["a", "b[0]", "b[1]"])
        self.assertEqual(tests[1]["prompt"], "say x")
        self.assertEqual(tests[0]["trials"], 3)
        self.assertEqual(tests[0]["timeout_s"], 9)
        self.assertEqual(len(core.filter_tests(tests, ids=["b"])), 2)

    def test_validation_errors(self):
        s = {"spec_version": 2, "suite": "x", "tests": [{"id": "a", "kind": "behavior", "prompt": "p"},
                                                          {"id": "a", "kind": "nope", "prompt": ""}], "_dir": "."}
        errs = " ".join(core.validate_suite(s))
        for frag in ("spec_version", "at least one grader", "duplicate id", "kind must be", "`prompt` is required"):
            self.assertIn(frag, errs)


class TraceParsing(unittest.TestCase):
    def test_real_claude_code_trace(self):
        lines = (DATA / "real_trace_cc2.1.224.jsonl").read_text().splitlines()
        s = run_suite.parse_trace(lines)
        self.assertEqual(s["skills_loaded"], ["haiku-poet"])
        self.assertIn("haiku-poet", s["visible_skills"])
        self.assertEqual([c["name"] for c in s["tool_calls"]], ["Skill", "Write"])
        self.assertTrue(s["files_written"][0].endswith("haiku.txt"))
        self.assertEqual(s["metrics"]["num_turns"], 4)
        self.assertGreater(s["metrics"]["cost_usd"], 0)
        self.assertEqual(s["runtime_version"], "2.1.224")
        self.assertIn("Soft patter", s["final_text"])
        self.assertTrue(run_suite.triggered(s, "haiku-poet"))
        self.assertFalse(run_suite.triggered(s, "other"))

    def test_plugin_skill_names_match(self):
        self.assertTrue(run_suite.skill_matches("myplugin:haiku-poet", "haiku-poet"))
        self.assertFalse(run_suite.skill_matches("haiku-poet-2", "haiku-poet"))

    def test_trace_grader(self):
        s = run_suite.parse_trace((DATA / "real_trace_cc2.1.224.jsonl").read_text().splitlines())
        g = run_suite.grade_trace({"must_load_skill": "haiku-poet", "tool_order": ["Skill", "Write"],
                                   "forbidden_tools": ["Bash(rm *)"], "max_tool_calls": 3}, s)
        self.assertTrue(g["passed"], g["evidence"])
        g = run_suite.grade_trace({"must_not_load_skill": "haiku-poet", "required_tools": ["Bash"]}, s)
        self.assertFalse(g["passed"])

    def test_mini_schema(self):
        sch = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer", "minimum": 1}}}
        self.assertEqual(run_suite._mini_schema_errors({"a": 2}, sch), [])
        self.assertTrue(run_suite._mini_schema_errors({"a": 0}, sch))
        self.assertTrue(run_suite._mini_schema_errors({}, sch))

    def test_redaction(self):
        self.assertNotIn("sk-ant", core.redact("key=sk-ant-api03-abcdefghijklmnop"))


class EndToEnd(unittest.TestCase):
    """Full runs through the fake CLI."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="skillcheck-test-"))
        cls.env = dict(os.environ, SKILLCHECK_CLAUDE_BIN=str(FAKE), SKILLCHECK_HOME=str(cls.tmp / "state"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_cli(self, script, *args, env=None, ok=(0,)):
        p = subprocess.run([PY, str(script)] + [str(a) for a in args], capture_output=True, text=True,
                           env=dict(self.env, **(env or {})), cwd=str(self.tmp))
        self.assertIn(p.returncode, ok, p.stdout + p.stderr)
        return p

    def suite_run(self, run_id, env=None, *extra, ok=(0,)):
        self.run_cli(RUN, "run", DATA / "suite" / "suite.yaml", "--out", self.tmp / "runs", "--run-id", run_id,
                     "--quiet", *extra, env=env, ok=ok)
        return self.tmp / "runs" / run_id

    def test_run_compare_gate_verify(self):
        base = self.suite_run("e2e-base")
        res = core.load_json(base / "results.json")
        self.assertEqual(res["status"], "completed")
        by = {t["id"]: t for t in res["tests"]}
        self.assertEqual(by["trig-yes"]["passed"], 6)
        self.assertEqual(by["trig-no"]["passed"], 6)
        self.assertEqual(by["writes-report"]["passed"], 6)
        tr = by["writes-report"]["trials"][0]
        self.assertTrue((base / tr["trial_dir"] / "trace.jsonl").exists())
        self.assertTrue((base / tr["trial_dir"] / "files" / "out" / "report.txt").exists())
        self.run_cli(ANALYZE, "verify", base)
        cand = self.suite_run("e2e-cand", {"FAKE_PASS_RATE": "0"})
        p = self.run_cli(ANALYZE, "gate", base, cand, "--json", ok=(1,))
        out = json.loads(p.stdout)
        self.assertTrue(out["gate"]["blocked"])
        rows = {r["id"]: r for r in out["tests"]}
        self.assertEqual(rows["writes-report"]["verdict"], "regression")
        self.assertIn(rows["judged"]["fdr_family"], ("informational",))
        aa = self.run_cli(ANALYZE, "gate", base, base, "--json")
        self.assertFalse(json.loads(aa.stdout)["gate"]["blocked"])

    def test_resume_redoes_only_missing_trials(self):
        rd = self.suite_run("e2e-resume")
        ev = rd / "events.jsonl"
        lines = ev.read_text().splitlines()
        trial_lines = [i for i, l in enumerate(lines) if '"trial_finished"' in l]
        ev.write_text("\n".join(lines[:trial_lines[-3]]) + "\n")  # simulate a crash: last 3 trials lost
        (rd / "results.json").unlink()
        self.run_cli(RUN, "resume", rd)
        recs = [json.loads(l) for l in ev.read_text().splitlines()]
        self.assertEqual(sum(r["type"] == "trial_finished" for r in recs), 30)
        self.assertEqual(sum(r["type"] == "run_resumed" for r in recs), 1)
        self.run_cli(ANALYZE, "verify", rd)

    def test_tamper_detection(self):
        rd = self.suite_run("e2e-tamper")
        ev = rd / "events.jsonl"
        ev.write_text(ev.read_text().replace('"passed":false', '"passed":true', 1).replace('"status":"failed"', '"status":"passed"', 1)
                      .replace('"cost_usd":0.01', '"cost_usd":0.02', 1))
        self.run_cli(ANALYZE, "verify", rd, ok=(1,))

    def test_infra_errors_are_excluded_not_failed(self):
        rd = self.suite_run("e2e-infra", {"FAKE_INFRA_ERROR": "1"}, "--retries", "0", "--test", "trig-yes")
        res = core.load_json(rd / "results.json")
        t = res["tests"][0]
        self.assertEqual((t["graded"], t["errors"]), (0, 6))

    def test_without_skill_lift(self):
        with_s = self.suite_run("e2e-with", None, "--kind", "behavior")
        without = self.suite_run("e2e-without", None, "--kind", "behavior", "--without-skill")
        res = core.load_json(without / "results.json")
        self.assertEqual(res["target"]["without_skill"], ["demo-skill"])
        p = self.run_cli(ANALYZE, "compare", without, with_s, "--lift", "--json")
        out = json.loads(p.stdout)
        self.assertEqual(out["mode"], "lift")
        self.assertIn("skill_presence", out["changed_axes"])

    def test_budget_abort(self):
        rd = self.suite_run("e2e-budget", None, "--max-run-usd", "0.05", "--concurrency", "1", ok=(3,))
        res = core.load_json(rd / "results.json")
        self.assertEqual(res["status"], "aborted_budget")
        self.assertLess(res["totals"]["trials"], 30)

    def test_baseline_and_drift_dedup(self):
        base = self.suite_run("e2e-drift-base")
        self.run_cli(ANALYZE, "baseline", "save", "nightly", base)
        cand = self.suite_run("e2e-drift-cand", {"FAKE_PASS_RATE": "0"})
        first = self.run_cli(ANALYZE, "drift", "baseline:nightly", cand, ok=(1,))
        self.assertIn("NEW", first.stdout)
        second = self.run_cli(ANALYZE, "drift", "baseline:nightly", cand)
        self.assertIn("0 new alert", second.stdout)

    def test_report_outputs(self):
        rd = self.suite_run("e2e-report")
        self.run_cli(ROOT / "skills" / "skillcheck-compare" / "scripts" / "report.py", rd,
                     "--html", self.tmp / "r.html", "--junit", self.tmp / "j.xml", "--md", self.tmp / "r.md")
        html = (self.tmp / "r.html").read_text()
        self.assertIn("writes-report", html)
        self.assertIn("<testsuite", (self.tmp / "j.xml").read_text())


class Packaging(unittest.TestCase):
    def test_core_copies_in_sync(self):
        p = subprocess.run([PY, str(ROOT / "tools" / "sync_core.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_our_own_skills_lint_clean(self):
        for d in sorted((ROOT / "skills").iterdir()):
            if (d / "SKILL.md").exists():
                sk = core.load_skill(d)
                errs = [f for f in core.lint_skill(sk) if f["level"] in ("error", "warning")]
                self.assertEqual(errs, [], d.name)


if __name__ == "__main__":
    unittest.main()
