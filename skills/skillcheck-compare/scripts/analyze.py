#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml>=5.4"]
# ///
"""skillcheck analysis: statistics, verdicts, gating, baselines, lift and drift.

  analyze.py summary RUN                       per-test pass rates with CIs, pass@k, pass^k,
                                               trigger precision/recall, cost, flakiness
  analyze.py compare BASE CAND [--lift]        verdict per test + suite (regression |
                                               improvement | no_significant_change | inconclusive)
  analyze.py gate BASE CAND [--policy gate.yaml]   CI gate; exit 1 when blocked
  analyze.py power [--p 0.9 --effect 0.2]      trials needed to detect an effect
  analyze.py baseline save NAME RUN | list | show NAME | delete NAME
  analyze.py drift BASE CAND [--webhook URL]   compare + de-duplicated alert state machine
  analyze.py drift-ack KEY | drift-status
  analyze.py flaky RUN [RUN ...]               flake report across runs, quarantine hints
  analyze.py verify RUN                        check the run's hash chain (tamper evidence)

RUN/BASE/CAND: a run directory, a results.json, or baseline:NAME.
Common options: --alpha 0.05 --min-effect 0.2 --json --out FILE
Python 3.9+, stdlib only (PyYAML only for YAML gate policies).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import statistics
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skillcheck_core as core  # noqa: E402

STATE_DIR = Path(os.environ.get("SKILLCHECK_HOME", ".skillcheck"))
DEFAULT_POLICY = {
    "alpha": 0.05, "min_effect": 0.2, "suite_min_effect": 0.1, "power": 0.8,
    "critical_tests": [], "block_on_suite_regression": True, "block_on_critical_regression": True,
    "block_on_any_regression": False, "inconclusive": "warn", "max_cost_increase_pct": None,
    "allow_uncalibrated_judges": False, "include_unreviewed": False, "include_quarantined": False,
    "min_trials": 5, "max_error_rate": 0.2,
}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_run(ref: str) -> Dict[str, Any]:
    if ref.startswith("baseline:"):
        p = STATE_DIR / "baselines" / (ref.split(":", 1)[1] + ".json")
        if not p.exists():
            raise SystemExit("no baseline named %r (see: analyze.py baseline list)" % ref.split(":", 1)[1])
        data = core.load_json(p)
        data.setdefault("_ref", ref)
        return data
    p = Path(ref)
    if p.is_dir():
        p = p / "results.json"
    if not p.exists():
        raise SystemExit("not found: %s" % p)
    data = core.load_json(p)
    if data.get("kind") != "skillcheck.run":
        raise SystemExit("%s is not a skillcheck results file" % p)
    if data.get("schema_version") != core.SCHEMA_VERSION:
        raise SystemExit("%s has schema_version %s; this tool reads %s" % (p, data.get("schema_version"), core.SCHEMA_VERSION))
    data["_ref"] = str(p)
    return data


def graded_trials(t: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [x for x in t["trials"] if x["status"] != "error"]


def counts(t: Dict[str, Any]) -> Tuple[int, int]:
    g = graded_trials(t)
    return sum(1 for x in g if x.get("passed")), len(g)


def mean(xs: List[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def median(xs: List[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def judge_info(t: Dict[str, Any]) -> Dict[str, Any]:
    cal = [g.get("calibrated") for tr in graded_trials(t) for g in tr.get("grades", []) if g["type"] == "llm_rubric"]
    return {"uses_judge": bool(cal) or t.get("uses_llm_judge", False), "calibrated": bool(cal) and all(cal)}


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def summarize(run: Dict[str, Any], alpha: float = 0.05) -> Dict[str, Any]:
    rows = []
    trig: Dict[str, Dict[str, int]] = {}
    for t in run["tests"]:
        k, n = counts(t)
        lo, hi = core.wilson_interval(k, n, alpha)
        kk = min(3, n) if n else 0
        g = graded_trials(t)
        costs = [(x.get("metrics") or {}).get("cost_usd") for x in g]
        durs = [((x.get("metrics") or {}).get("duration_ms") or 0) / 1000 for x in g]
        toks = [(x.get("metrics") or {}).get("total_tokens") for x in g]
        calls = [(x.get("metrics") or {}).get("tool_calls") for x in g]
        flaky = 0 < k < n
        rows.append({
            "id": t["id"], "kind": t["kind"], "critical": t["critical"], "quarantine": t["quarantine"],
            "reviewed": t["reviewed"], "passed": k, "trials": n, "errors": t["errors"],
            "pass_rate": (k / n) if n else None, "ci": [lo, hi],
            "pass_at_1": core.pass_at_k(n, k, 1), "k": kk,
            "pass_at_k": core.pass_at_k(n, k, kk) if kk else None,
            "pass_hat_k": core.pass_hat_k(n, k, kk) if kk else None,
            "flaky": flaky, "mean_cost_usd": mean(costs), "median_duration_s": median(durs),
            "median_tokens": median(toks), "median_tool_calls": median(calls), **judge_info(t),
            "verdict_possible": n >= 2,
        })
        if t["kind"] == "trigger":
            for x in g:
                tg = next((gr for gr in x.get("grades", []) if gr["type"] == "trigger"), None)
                if not tg:
                    continue
                d = trig.setdefault(tg["skill"], {"tp": 0, "fp": 0, "tn": 0, "fn": 0})
                key = ("t" if tg["triggered"] == tg["expected"] else "f") + ("p" if tg["triggered"] else "n")
                d[key] += 1
    trigger = {}
    for skill, d in trig.items():
        prec_n, rec_n = d["tp"] + d["fp"], d["tp"] + d["fn"]
        trigger[skill] = {**d,
                          "precision": d["tp"] / prec_n if prec_n else None,
                          "precision_ci": core.wilson_interval(d["tp"], prec_n, alpha) if prec_n else None,
                          "recall": d["tp"] / rec_n if rec_n else None,
                          "recall_ci": core.wilson_interval(d["tp"], rec_n, alpha) if rec_n else None}
    inter = [t for t in run["tests"] if t["kind"] == "interaction"]
    ik = sum(counts(t)[0] for t in inter)
    inn = sum(counts(t)[1] for t in inter)
    warnings = []
    if run.get("model_changed_mid_run"):
        warnings.append("model changed mid-run: %s (results mix two models)" % run.get("observed_models"))
    if run.get("status") != "completed":
        warnings.append("run status is %s; results are partial" % run.get("status"))
    if any(not r["reviewed"] for r in rows):
        warnings.append("%d unreviewed test(s); they are excluded from gating" % sum(not r["reviewed"] for r in rows))
    if any(r["uses_judge"] and not r["calibrated"] for r in rows):
        warnings.append("uncalibrated LLM judge(s) in use; their tests are excluded from gating by default")
    if any(r["trials"] == 1 for r in rows):
        warnings.append("some tests have a single graded trial: no pass/fail verdict is reported for them")
    under_test = {s["name"] for s in run.get("skills", [])} | {s["name"] for s in run.get("distractors", [])}
    extra = set()
    for t in run["tests"]:
        for x in graded_trials(t):
            loaded = set(x.get("skills_loaded") or [])
            extra |= {s for s in loaded if not any(s == n or s.endswith(":" + n) for n in under_test)}
    if extra:
        warnings.append("skills outside the suite were loaded during trials: %s (personal/built-in skills "
                        "can interfere; consider --clean-config)" % ", ".join(sorted(extra)))
    err_total = sum(t["errors"] for t in run["tests"])
    trials_total = sum(len(t["trials"]) for t in run["tests"])
    if trials_total and err_total / trials_total > 0.1:
        warnings.append("%.0f%% of trials hit infrastructure errors" % (100 * err_total / trials_total))
    return {
        "kind": "skillcheck.summary", "schema_version": core.SCHEMA_VERSION, "run_id": run["run_id"],
        "suite": run["suite"]["name"], "status": run.get("status"), "models": run.get("observed_models"),
        "runtime_versions": run.get("observed_runtime_versions"),
        "skills": [{"name": s["name"], "content_hash": s["content_hash"], "git_sha": s.get("git_sha"),
                    "footprint": s.get("footprint")} for s in run.get("skills", [])],
        "without_skill": run["target"].get("without_skill"), "alpha": alpha, "tests": rows,
        "trigger": trigger,
        "interaction": {"passed": ik, "trials": inn, "accuracy": ik / inn if inn else None,
                        "ci": core.wilson_interval(ik, inn, alpha) if inn else None},
        "totals": run["totals"], "warnings": warnings,
    }


def fmt_ci(ci: Optional[List[float]]) -> str:
    return "[%.2f, %.2f]" % tuple(ci) if ci else "-"


def print_summary(s: Dict[str, Any]) -> None:
    print("run %s  suite %s  status %s" % (s["run_id"], s["suite"], s["status"]))
    print("models %s  runtime %s" % (", ".join(s["models"] or ["?"]), ", ".join(s["runtime_versions"] or ["?"])))
    for sk in s["skills"]:
        print("skill %s %s%s" % (sk["name"], sk["content_hash"][:19], "  (removed for this run)" if sk["name"] in (s["without_skill"] or []) else ""))
    print("\n%-40s %-11s %-8s %-14s %-7s %-7s %-8s %s" % ("test", "kind", "pass", "95% CI", "pass@k", "pass^k", "$/trial", "flags"))
    for r in s["tests"]:
        flags = [f for f, on in (("critical", r["critical"]), ("quarantined", r["quarantine"]), ("unreviewed", not r["reviewed"]),
                                 ("flaky", r["flaky"]), ("uncalibrated-judge", r["uses_judge"] and not r["calibrated"]),
                                 ("%d err" % r["errors"], r["errors"]), ("n=1: no verdict", r["trials"] == 1)) if on]
        print("%-40s %-11s %3d/%-4d %-14s %-7s %-7s %-8s %s" % (
            r["id"][:40], r["kind"], r["passed"], r["trials"], fmt_ci(r["ci"]),
            "%.2f" % r["pass_at_k"] if r["pass_at_k"] is not None else "-",
            "%.2f" % r["pass_hat_k"] if r["pass_hat_k"] is not None else "-",
            "%.3f" % r["mean_cost_usd"] if r["mean_cost_usd"] is not None else "-", ", ".join(flags)))
    for skill, d in s["trigger"].items():
        print("\ntrigger %s: precision %s %s  recall %s %s  (tp %d fp %d tn %d fn %d)" % (
            skill, "%.2f" % d["precision"] if d["precision"] is not None else "-", fmt_ci(d["precision_ci"]),
            "%.2f" % d["recall"] if d["recall"] is not None else "-", fmt_ci(d["recall_ci"]),
            d["tp"], d["fp"], d["tn"], d["fn"]))
    if s["interaction"]["trials"]:
        print("interaction: correct skill selected %d/%d %s" % (s["interaction"]["passed"], s["interaction"]["trials"], fmt_ci(s["interaction"]["ci"])))
    print("\ntotal cost $%.3f over %d trials (%d errors)" % (s["totals"]["cost_usd"], s["totals"]["trials"], s["totals"]["errors"]))
    print("pass@k = chance at least 1 of k tries passes; pass^k = chance all k pass (k=min(3,n))")
    for w in s["warnings"]:
        print("WARNING: " + w)


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

def axis_changes(base: Dict[str, Any], cand: Dict[str, Any]) -> Dict[str, Any]:
    def skill_map(r):
        return {s["name"]: s["content_hash"] for s in r.get("skills", [])}
    ch = {}
    bs, cs = skill_map(base), skill_map(cand)
    changed = sorted(n for n in set(bs) | set(cs) if bs.get(n) != cs.get(n))
    if changed:
        ch["skill"] = {n: [bs.get(n, "absent")[:19], cs.get(n, "absent")[:19]] for n in changed}
    bw, cw = base["target"].get("without_skill") or [], cand["target"].get("without_skill") or []
    if bw != cw:
        ch["skill_presence"] = [bw, cw]
    if (base.get("observed_models") or []) != (cand.get("observed_models") or []):
        ch["model"] = [base.get("observed_models"), cand.get("observed_models")]
    if (base.get("observed_runtime_versions") or []) != (cand.get("observed_runtime_versions") or []):
        ch["runtime"] = [base.get("observed_runtime_versions"), cand.get("observed_runtime_versions")]
    bd = {s["name"]: s["content_hash"] for s in base.get("distractors", [])}
    cd = {s["name"]: s["content_hash"] for s in cand.get("distractors", [])}
    if bd != cd:
        ch["distractors"] = [sorted(bd), sorted(cd)]
    if base["suite"]["sha256"] != cand["suite"]["sha256"]:
        ch["suite"] = [base["suite"]["sha256"][:19], cand["suite"]["sha256"][:19]]
    return ch


def family_of(r: Dict[str, Any]) -> str:
    if r["quarantine"] or not r["reviewed"] or (r.get("judge_uses_judge") and not r.get("judge_calibrated")):
        return "informational"
    return "critical" if r["critical"] else "gated"


def compare_runs(base: Dict[str, Any], cand: Dict[str, Any], alpha: float, min_effect: float,
                 suite_min_effect: float, power: float, lift: bool = False, seed: int = 0) -> Dict[str, Any]:
    bt = {t["id"]: t for t in base["tests"]}
    ct = {t["id"]: t for t in cand["tests"]}
    common = [i for i in ct if i in bt]
    only_base = sorted(set(bt) - set(ct))
    only_cand = sorted(set(ct) - set(bt))
    rows = []
    for tid in common:
        b, c = bt[tid], ct[tid]
        if lift and c["kind"] in ("trigger", "interaction"):
            continue
        kb, nb = counts(b)
        kc, nc = counts(c)
        row = {"id": tid, "kind": c["kind"], "critical": c["critical"] or b["critical"],
               "quarantine": c["quarantine"], "reviewed": c["reviewed"], **{"judge_" + k: v for k, v in judge_info(c).items()}}
        if nb == 0 or nc == 0:
            row.update({"verdict": "inconclusive", "reason": "no graded trials on one side", "p_value": 1.0,
                        "effect": 0.0, "ci": [-1, 1], "mde": 1.0,
                        "base": {"passed": kb, "trials": nb}, "cand": {"passed": kc, "trials": nc}})
        else:
            row.update(core.compare_binary(kb, nb, kc, nc, alpha, min_effect, power))
        bc = mean([(x.get("metrics") or {}).get("cost_usd") for x in graded_trials(b)])
        cc = mean([(x.get("metrics") or {}).get("cost_usd") for x in graded_trials(c)])
        bd = median([((x.get("metrics") or {}).get("duration_ms") or 0) / 1000 for x in graded_trials(b)])
        cd = median([((x.get("metrics") or {}).get("duration_ms") or 0) / 1000 for x in graded_trials(c)])
        row["cost"] = {"base": bc, "cand": cc}
        row["duration_s"] = {"base": bd, "cand": cd}
        rows.append(row)
    testable = [r for r in rows if "reason" not in r]
    # FDR is controlled within separate families so tests that are never gated
    # (quarantined, unreviewed, uncalibrated judge) do not dilute the power of
    # gated ones, and critical tests are judged on their own (brief 5.5).
    for r in testable:
        r["fdr_family"] = family_of(r)
    for fam in ("critical", "gated", "informational"):
        members = [r for r in testable if r["fdr_family"] == fam]
        for r, q in zip(members, core.benjamini_hochberg([r["p_value"] for r in members])):
            r["q_value"] = q
            r["verdict"] = core.decide_verdict(r["effect"], q, r["mde"], alpha, min_effect, r["ci"])
            if min(r["base"]["trials"], r["cand"]["trials"]) < 2:  # never judge a single trial
                r["verdict"] = "inconclusive"
    for r in rows:
        r.setdefault("q_value", 1.0)

    suite_rows = [r for r in rows if not r["quarantine"] and r["reviewed"] and "reason" not in r]
    diffs = [r["effect"] for r in suite_rows]
    m, lo, hi = core.paired_bootstrap_ci(diffs, alpha, seed=seed)
    p = core.paired_bootstrap_pvalue(diffs, seed=seed) if diffs else 1.0
    down = sum(1 for d in diffs if d < 0)
    up = sum(1 for d in diffs if d > 0)
    sign_p = core.mcnemar_exact(down, up)
    suite_verdict = core.decide_verdict(m, p, float("inf"), alpha, suite_min_effect, [lo, hi]) if diffs else "inconclusive"
    if len(diffs) < 5 and suite_verdict in ("regression", "improvement"):
        suite_verdict += "_weak"  # bootstrap over < 5 tests is unreliable
    costs_b = [r["cost"]["base"] for r in suite_rows if r["cost"]["base"] and r["cost"]["cand"] is not None]
    costs_c = [r["cost"]["cand"] for r in suite_rows if r["cost"]["base"] and r["cost"]["cand"] is not None]
    cost_change = (sum(costs_c) / sum(costs_b) - 1) if costs_b and sum(costs_b) else None
    cost_diffs = [c - b for b, c in zip(costs_b, costs_c)]
    cm, clo, chi = core.paired_bootstrap_ci(cost_diffs, alpha, seed=seed) if cost_diffs else (0, 0, 0)

    sb, sc = summarize(base, alpha), summarize(cand, alpha)
    trig = {}
    for skill in set(sb["trigger"]) | set(sc["trigger"]):
        x, y = sb["trigger"].get(skill), sc["trigger"].get(skill)
        if not x or not y:
            continue
        trig[skill] = {}
        for metric, num, den in (("precision", "tp", ("tp", "fp")), ("recall", "tp", ("tp", "fn"))):
            nb, nc = x[den[0]] + x[den[1]], y[den[0]] + y[den[1]]
            if nb and nc:
                cmp = core.compare_binary(x[num], nb, y[num], nc, alpha, min_effect, power)
                cmp["verdict"] = core.decide_verdict(cmp["effect"], cmp["p_value"], cmp["mde"], alpha, min_effect, cmp["ci"])
                trig[skill][metric] = cmp
    counts_by = {v: sum(1 for r in rows if r["verdict"] == v) for v in core.VERDICTS}
    underpowered = [r["id"] for r in rows if r.get("mde", 1) > min_effect]
    return {
        "kind": "skillcheck.comparison", "schema_version": core.SCHEMA_VERSION, "mode": "lift" if lift else "regression",
        "created_at": now(), "base": {"ref": base["_ref"], "run_id": base["run_id"]},
        "cand": {"ref": cand["_ref"], "run_id": cand["run_id"]}, "changed_axes": axis_changes(base, cand),
        "params": {"alpha": alpha, "min_effect": min_effect, "suite_min_effect": suite_min_effect, "power": power,
                   "per_test_test": "Fisher exact (two-sided), Newcombe hybrid-score CI, BH FDR within families (critical | gated | informational)",
                   "suite_test": "paired bootstrap over tests (10k resamples) + exact sign test"},
        "suite": {"verdict": suite_verdict, "mean_effect": m, "ci": [lo, hi], "p_value": p,
                  "tests_down": down, "tests_up": up, "sign_test_p": sign_p, "n_tests": len(diffs)},
        "cost": {"total_change_pct": None if cost_change is None else 100 * cost_change,
                 "mean_diff_usd_per_trial": cm, "ci": [clo, chi]},
        "trigger": trig, "tests": rows, "verdict_counts": counts_by,
        "only_in_base": only_base, "only_in_cand": only_cand,
        "underpowered_tests": underpowered,
        "power_hint": _power_hint(rows, min_effect, alpha, power),
    }


def _power_hint(rows: List[Dict[str, Any]], min_effect: float, alpha: float, power: float) -> Optional[str]:
    under = [r for r in rows if r.get("mde", 1) > min_effect and "base" in r and r["base"].get("trials")]
    if not under:
        return None
    rates = [r["base"]["passed"] / r["base"]["trials"] for r in under if r["base"]["trials"]]
    p = max(rates) if rates else 0.9
    need = core.trials_needed(p, min_effect, alpha, power)
    return ("%d test(s) cannot detect a %.0f-point drop at current trial counts; ~%s trials per side "
            "would be needed (baseline pass rate %.2f). Gate on the suite verdict, raise trials on critical "
            "tests, or raise --min-effect." % (len(under), 100 * min_effect, need or ">2000", p))


VERDICT_ICON = {"regression": "🔴", "improvement": "🟢", "no_significant_change": "⚪", "inconclusive": "🟡",
                "regression_weak": "🟠", "improvement_weak": "🟢"}


def comparison_markdown(c: Dict[str, Any], gate: Optional[Dict[str, Any]] = None) -> str:
    L = []
    title = "Skill lift (with vs without skill)" if c["mode"] == "lift" else "skillcheck comparison"
    L.append("## %s\n" % title)
    if gate:
        L.append("**Gate: %s**%s\n" % ("❌ BLOCKED" if gate["blocked"] else "✅ PASSED",
                                       "" if not gate["warnings"] else " (with %d warning(s))" % len(gate["warnings"])))
        for b in gate["blockers"]:
            L.append("- ❌ " + b)
        for w in gate["warnings"]:
            L.append("- ⚠️ " + w)
        L.append("")
    s = c["suite"]
    L.append("**Suite verdict: %s %s** — mean pass-rate change %+.1f pts (95%% CI %+.1f to %+.1f), "
             "%d test(s) down, %d up, p=%.3g\n" % (VERDICT_ICON.get(s["verdict"], ""), s["verdict"], 100 * s["mean_effect"],
                                                  100 * s["ci"][0], 100 * s["ci"][1], s["tests_down"], s["tests_up"], s["p_value"]))
    if c["changed_axes"]:
        L.append("Changed between runs: " + "; ".join("**%s** %s" % (k, json.dumps(v)) for k, v in c["changed_axes"].items()) + "\n")
    else:
        L.append("No recorded input changed (skill, model, runtime, suite): treat this as an A/A check. "
                 "A regression here means nondeterminism or an unrecorded change (provider-side drift, environment).\n")
    if c["cost"]["total_change_pct"] is not None:
        L.append("Cost per trial: %+.1f%% (mean diff $%+.4f, CI $%+.4f to $%+.4f)\n" % (
            c["cost"]["total_change_pct"], c["cost"]["mean_diff_usd_per_trial"], c["cost"]["ci"][0], c["cost"]["ci"][1]))
    L.append("| test | kind | base | cand | change | 95% CI | q | verdict |")
    L.append("|---|---|---|---|---|---|---|---|")
    order = {"regression": 0, "inconclusive": 1, "improvement": 2, "no_significant_change": 3}
    for r in sorted(c["tests"], key=lambda r: (order.get(r["verdict"], 9), r["id"])):
        flags = "".join([" ⭐" if r["critical"] else "", " 🧊" if r["quarantine"] else "", " 📝" if not r["reviewed"] else ""])
        L.append("| `%s`%s | %s | %d/%d | %d/%d | %+.0f pts | %+.0f…%+.0f | %.3f | %s %s |" % (
            r["id"], flags, r["kind"], r["base"]["passed"], r["base"]["trials"], r["cand"]["passed"], r["cand"]["trials"],
            100 * r["effect"], 100 * r["ci"][0], 100 * r["ci"][1], r["q_value"], VERDICT_ICON.get(r["verdict"], ""), r["verdict"]))
    for skill, d in c["trigger"].items():
        parts = []
        for metric, v in d.items():
            parts.append("%s %.2f→%.2f (%s)" % (metric, v["base"]["rate"], v["cand"]["rate"], v["verdict"]))
        L.append("\nTrigger `%s`: %s" % (skill, "; ".join(parts)))
    if c["power_hint"]:
        L.append("\n> Power: " + c["power_hint"])
    if c["only_in_base"] or c["only_in_cand"]:
        L.append("\nTests only in base: %s; only in candidate: %s" % (c["only_in_base"] or "-", c["only_in_cand"] or "-"))
    L.append("\n<sub>⭐ critical · 🧊 quarantined · 📝 unreviewed. Per-test: Fisher exact + Newcombe CI, "
             "Benjamini-Hochberg FDR at α=%.2f within critical / gated / informational families; suite: paired bootstrap over tests. "
             "`inconclusive` = not enough trials to rule out a %.0f-point change.</sub>" % (c["params"]["alpha"], 100 * c["params"]["min_effect"]))
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def load_policy(path: Optional[str]) -> Dict[str, Any]:
    pol = dict(DEFAULT_POLICY)
    if path:
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        if p.suffix == ".json":
            data = json.loads(text)
        else:
            if core.yaml is None:
                raise SystemExit("YAML policy needs PyYAML; use JSON or `uv run`")
            data = core.yaml.safe_load(text) or {}
        unknown = set(data) - set(DEFAULT_POLICY)
        if unknown:
            raise SystemExit("unknown gate policy keys: %s" % ", ".join(sorted(unknown)))
        pol.update(data)
    return pol


def gate(c: Dict[str, Any], cand: Dict[str, Any], pol: Dict[str, Any]) -> Dict[str, Any]:
    blockers, warnings, excluded = [], [], []
    crit_ids = set(pol["critical_tests"] or [])
    for r in c["tests"]:
        why = None
        if r["quarantine"] and not pol["include_quarantined"]:
            why = "quarantined"
        elif not r["reviewed"] and not pol["include_unreviewed"]:
            why = "unreviewed"
        elif r.get("judge_uses_judge") and not r.get("judge_calibrated") and not pol["allow_uncalibrated_judges"]:
            why = "uncalibrated LLM judge"
        if why:
            excluded.append({"id": r["id"], "reason": why})
            continue
        critical = r["critical"] or r["id"] in crit_ids or r["id"].split("[")[0] in crit_ids
        if r["verdict"] == "regression":
            if critical and pol["block_on_critical_regression"]:
                blockers.append("critical test `%s` regressed %+.0f pts (q=%.3f)" % (r["id"], 100 * r["effect"], r["q_value"]))
            elif pol["block_on_any_regression"]:
                blockers.append("test `%s` regressed %+.0f pts (q=%.3f)" % (r["id"], 100 * r["effect"], r["q_value"]))
            else:
                warnings.append("test `%s` regressed %+.0f pts (q=%.3f)" % (r["id"], 100 * r["effect"], r["q_value"]))
        if critical and r["verdict"] == "inconclusive":
            msg = "critical test `%s` is inconclusive (can only detect a %.0f-pt change with these trials)" % (r["id"], 100 * r.get("mde", 1))
            (blockers if pol["inconclusive"] == "block" else warnings).append(msg)
        if critical and min(r["base"]["trials"], r["cand"]["trials"]) < pol["min_trials"]:
            warnings.append("critical test `%s` has fewer than %d trials" % (r["id"], pol["min_trials"]))
    sv = c["suite"]["verdict"]
    if sv.startswith("regression") and pol["block_on_suite_regression"]:
        blockers.append("suite regressed: mean %+.1f pts (CI %+.1f…%+.1f)" % (100 * c["suite"]["mean_effect"],
                                                                               100 * c["suite"]["ci"][0], 100 * c["suite"]["ci"][1]))
    if sv == "inconclusive":
        (blockers if pol["inconclusive"] == "block" else warnings).append("suite verdict is inconclusive")
    if pol["max_cost_increase_pct"] is not None and (c["cost"]["total_change_pct"] or 0) > pol["max_cost_increase_pct"]:
        blockers.append("cost per trial up %.0f%% (limit %s%%)" % (c["cost"]["total_change_pct"], pol["max_cost_increase_pct"]))
    tot = cand["totals"]
    if tot["trials"] and tot["errors"] / tot["trials"] > pol["max_error_rate"]:
        blockers.append("%.0f%% of candidate trials were infrastructure errors" % (100 * tot["errors"] / tot["trials"]))
    if cand.get("status") != "completed":
        blockers.append("candidate run did not complete (%s)" % cand.get("status"))
    if cand.get("model_changed_mid_run"):
        warnings.append("model changed during the candidate run")
    return {"blocked": bool(blockers), "blockers": blockers, "warnings": warnings, "excluded": excluded, "policy": pol}


# ---------------------------------------------------------------------------
# Drift alert state machine
# ---------------------------------------------------------------------------

def drift_update(c: Dict[str, Any], suite: str, state_path: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """open -> acknowledged -> resolved. Alerts fire only on a transition into `open`."""
    state = core.load_json(state_path) if state_path.exists() else {"alerts": {}}
    fired = []
    signals = {}
    for r in c["tests"]:
        signals["%s/test/%s" % (suite, r["id"])] = (r["verdict"] == "regression", r)
    for skill, d in c["trigger"].items():
        for metric, v in d.items():
            signals["%s/trigger/%s/%s" % (suite, skill, metric)] = (v["verdict"] == "regression", v)
    signals["%s/suite" % suite] = (c["suite"]["verdict"].startswith("regression"), c["suite"])
    for key, (bad, detail) in signals.items():
        a = state["alerts"].get(key)
        if bad:
            if not a or a["state"] == "resolved":
                a = {"state": "open", "opened_at": now(), "count": 1, "last_seen": now(),
                     "detail": _brief(detail), "base": c["base"]["run_id"], "cand": c["cand"]["run_id"]}
                state["alerts"][key] = a
                fired.append({"key": key, **a})
            else:
                a["count"] += 1
                a["last_seen"] = now()
                a["detail"] = _brief(detail)
        elif a and a["state"] in ("open", "acknowledged"):
            a["state"] = "resolved"
            a["resolved_at"] = now()
    state["updated_at"] = now()
    core.write_json(state_path, state)
    return fired, state


def _brief(d: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("effect", "mean_effect", "ci", "q_value", "p_value", "verdict")
    return {k: d[k] for k in keys if k in d}


def notify(webhook: str, text: str) -> None:
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print("webhook failed: %s" % e, file=sys.stderr)


# ---------------------------------------------------------------------------
# Flaky + verify
# ---------------------------------------------------------------------------

def flaky_report(runs: List[Dict[str, Any]], alpha: float) -> List[Dict[str, Any]]:
    """Separate flakiness (mixed results inside runs) from shifts (rates differ between runs)."""
    per: Dict[str, List[Tuple[int, int]]] = {}
    for run in runs:
        for t in run["tests"]:
            k, n = counts(t)
            if n:
                per.setdefault(t["id"], []).append((k, n))
    out = []
    for tid, rs in sorted(per.items()):
        k, n = sum(x[0] for x in rs), sum(x[1] for x in rs)
        lo, hi = core.wilson_interval(k, n, alpha)
        flaky_runs = sum(1 for kk, nn in rs if 0 < kk < nn)
        shift_p = 1.0
        if len(rs) >= 2:
            a_, b_ = min(rs, key=lambda x: x[0] / x[1]), max(rs, key=lambda x: x[0] / x[1])
            shift_p = core.fisher_exact_two_sided(a_[0], a_[1], b_[0], b_[1])
        shifted = shift_p < alpha
        if shifted:
            status = "shifted"
        elif flaky_runs >= max(1, (len(rs) + 1) // 2) and lo > 0.05 and hi < 0.95:
            status = "quarantine?"
        elif flaky_runs:
            status = "flaky"
        else:
            status = "stable"
        out.append({"id": tid, "passed": k, "trials": n, "pass_rate": k / n, "ci": [lo, hi],
                    "per_run": [kk / nn for kk, nn in rs], "flaky_runs": flaky_runs, "runs": len(rs),
                    "shift_p": shift_p, "status": status, "flaky": flaky_runs > 0,
                    "suggest_quarantine": status == "quarantine?"})
    return out


def verify_run(ref: str) -> Tuple[bool, str]:
    p = Path(ref)
    rd = p if p.is_dir() else p.parent
    ev = rd / "events.jsonl"
    if not ev.exists():
        return False, "no events.jsonl in %s" % rd
    recs = [json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines() if l.strip()]
    ok, head, bad = core.verify_chain(recs)
    if not ok:
        return False, "hash chain broken at record %d" % bad
    msgs = ["hash chain ok: %d events, head %s" % (len(recs), head[:23])]
    start = next((r for r in recs if r.get("type") == "run_started"), None)
    if start and start.get("manifest_sha256") != core.sha256_file(rd / "manifest.json"):
        return False, "manifest.json changed after the run started"
    res = rd / "results.json"
    if res.exists():
        r = core.load_json(res)
        if r.get("chain_head") != head:
            return False, "results.json chain_head does not match the event log (results edited or stale)"
        msgs.append("results.json matches the log")
    return True, "; ".join(msgs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def emit(obj: Any, text: str, a: argparse.Namespace) -> None:
    if getattr(a, "out", None):
        core.write_json(Path(a.out), obj)
    if getattr(a, "json", False):
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(text)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def stats_opts(p):
        p.add_argument("--alpha", type=float, default=0.05)
        p.add_argument("--min-effect", type=float, default=0.2, help="smallest per-test pass-rate change you care about")
        p.add_argument("--suite-min-effect", type=float, default=0.1)
        p.add_argument("--power", type=float, default=0.8)
        p.add_argument("--json", action="store_true")
        p.add_argument("--out", help="also write JSON here")
        p.add_argument("--md", help="also write a markdown report (PR comment) here")

    s = sub.add_parser("summary")
    s.add_argument("run")
    s.add_argument("--alpha", type=float, default=0.05)
    s.add_argument("--json", action="store_true")
    s.add_argument("--out")
    c = sub.add_parser("compare")
    c.add_argument("base")
    c.add_argument("cand")
    c.add_argument("--lift", action="store_true", help="base = run without the skill, cand = with it")
    stats_opts(c)
    g = sub.add_parser("gate")
    g.add_argument("base")
    g.add_argument("cand")
    g.add_argument("--policy")
    stats_opts(g)
    pw = sub.add_parser("power")
    pw.add_argument("--p", type=float, default=0.9, help="baseline pass rate")
    pw.add_argument("--effect", type=float, default=0.2)
    pw.add_argument("--alpha", type=float, default=0.05)
    pw.add_argument("--power", type=float, default=0.8)
    pw.add_argument("--trials", type=int, help="show the detectable effect for this many trials")
    b = sub.add_parser("baseline")
    b.add_argument("action", choices=["save", "list", "show", "delete"])
    b.add_argument("name", nargs="?")
    b.add_argument("run", nargs="?")
    b.add_argument("--force", action="store_true")
    d = sub.add_parser("drift")
    d.add_argument("base")
    d.add_argument("cand")
    d.add_argument("--webhook", default=os.environ.get("SKILLCHECK_WEBHOOK"))
    stats_opts(d)
    da = sub.add_parser("drift-ack")
    da.add_argument("key")
    sub.add_parser("drift-status")
    f = sub.add_parser("flaky")
    f.add_argument("runs", nargs="+")
    f.add_argument("--alpha", type=float, default=0.05)
    f.add_argument("--json", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("run")
    a = ap.parse_args(argv)

    if a.cmd == "summary":
        sm = summarize(load_run(a.run), a.alpha)
        if a.out:
            core.write_json(Path(a.out), sm)
        if a.json:
            print(json.dumps(sm, indent=2, default=str))
        else:
            print_summary(sm)
        return 0

    if a.cmd in ("compare", "gate", "drift"):
        base, cand = load_run(a.base), load_run(a.cand)
        cmp = compare_runs(base, cand, a.alpha, a.min_effect, a.suite_min_effect, a.power,
                           lift=getattr(a, "lift", False))
        if a.cmd == "compare":
            md = comparison_markdown(cmp)
            if a.md:
                Path(a.md).write_text(md, encoding="utf-8")
            emit(cmp, md, a)
            return 0
        if a.cmd == "gate":
            pol = load_policy(a.policy)
            if a.policy:
                cmp = compare_runs(base, cand, pol["alpha"], pol["min_effect"], pol["suite_min_effect"], pol["power"])
            gr = gate(cmp, cand, pol)
            cmp["gate"] = gr
            md = comparison_markdown(cmp, gr)
            if a.md:
                Path(a.md).write_text(md, encoding="utf-8")
            emit(cmp, md, a)
            return 1 if gr["blocked"] else 0
        fired, state = drift_update(cmp, cand["suite"]["name"], STATE_DIR / "drift" / "state.json")
        md = comparison_markdown(cmp)
        if a.md:
            Path(a.md).write_text(md, encoding="utf-8")
        lines = ["drift check %s vs %s: %d new alert(s); %d open" % (
            base["run_id"], cand["run_id"], len(fired), sum(1 for x in state["alerts"].values() if x["state"] == "open"))]
        for fa in fired:
            lines.append("  NEW %s %s" % (fa["key"], json.dumps(fa["detail"], default=str)))
        if fired and a.webhook:
            notify(a.webhook, "skillcheck drift: %d new alert(s) for suite %s (models %s)\n%s" % (
                len(fired), cand["suite"]["name"], cand.get("observed_models"),
                "\n".join("• %s %s" % (x["key"], x["detail"].get("verdict", "")) for x in fired)))
        emit({"comparison": cmp, "fired": fired}, "\n".join(lines), a)
        return 1 if fired else 0

    if a.cmd == "power":
        if a.trials:
            mde = core.min_detectable_effect(a.p, a.trials, a.trials, a.alpha, a.power)
            print("with %d trials per side at baseline pass rate %.2f, the smallest reliably detectable "
                  "drop is ~%.0f points (alpha %.2f, power %.0f%%)" % (a.trials, a.p, 100 * mde, a.alpha, 100 * a.power))
        n = core.trials_needed(a.p, a.effect, a.alpha, a.power)
        print("to detect a %.0f-point drop from %.2f: ~%s trials per side (per test). Suite-level pooling "
              "across tests needs far fewer per test." % (100 * a.effect, a.p, n or ">2000"))
        return 0

    if a.cmd == "baseline":
        bdir = STATE_DIR / "baselines"
        if a.action == "list":
            for p in sorted(bdir.glob("*.json")) if bdir.exists() else []:
                r = core.load_json(p)
                print("%-20s run %s  suite %s  models %s  pinned %s" % (p.stem, r["run_id"], r["suite"]["name"],
                                                                      r.get("observed_models"), r.get("baseline_pinned_at")))
            return 0
        if not a.name:
            raise SystemExit("baseline %s needs a NAME" % a.action)
        p = bdir / (a.name + ".json")
        if a.action == "show":
            print(json.dumps({k: v for k, v in core.load_json(p).items() if k != "tests"}, indent=2))
            return 0
        if a.action == "delete":
            p.unlink()
            print("deleted baseline %s" % a.name)
            return 0
        if not a.run:
            raise SystemExit("baseline save NAME RUN")
        if p.exists() and not a.force:
            raise SystemExit("baseline %s exists; --force to replace" % a.name)
        run = load_run(a.run)
        if run.get("status") != "completed":
            raise SystemExit("refusing to pin a run with status %s" % run.get("status"))
        ok, msg = verify_run(run["_ref"])
        if not ok:
            raise SystemExit("refusing to pin: %s" % msg)
        run["baseline_name"] = a.name
        run["baseline_pinned_at"] = now()
        run["baseline_source"] = run.pop("_ref")
        core.write_json(p, run)
        print("pinned baseline %s -> run %s (%s)" % (a.name, run["run_id"], p))
        return 0

    if a.cmd == "drift-ack":
        sp = STATE_DIR / "drift" / "state.json"
        state = core.load_json(sp)
        if a.key not in state["alerts"]:
            raise SystemExit("no alert %s" % a.key)
        state["alerts"][a.key]["state"] = "acknowledged"
        state["alerts"][a.key]["acknowledged_at"] = now()
        core.write_json(sp, state)
        print("acknowledged %s" % a.key)
        return 0
    if a.cmd == "drift-status":
        sp = STATE_DIR / "drift" / "state.json"
        if not sp.exists():
            print("no drift state yet")
            return 0
        for k, v in sorted(core.load_json(sp)["alerts"].items()):
            print("%-14s %-60s since %s (seen %dx)" % (v["state"], k, v["opened_at"], v["count"]))
        return 0

    if a.cmd == "flaky":
        rep = flaky_report([load_run(r) for r in a.runs], a.alpha)
        if a.json:
            print(json.dumps(rep, indent=2))
        else:
            print("%-44s %-9s %-14s %s" % ("test", "pass", "95% CI", "status"))
            for r in rep:
                st = r["status"].upper() if r["status"] in ("shifted", "quarantine?") else r["status"]
                print("%-44s %3d/%-5d %-14s %s  per-run %s" % (r["id"][:44], r["passed"], r["trials"], fmt_ci(r["ci"]), st,
                                                              ["%.2f" % x for x in r["per_run"]]))
            print("\nSHIFTED = pass rate differs between runs (p<%.2f): a change to investigate, not flakiness." % a.alpha)
            print("Quarantine = add `quarantine: true` + `quarantine_reason` to the test. It still runs and is "
                  "reported, but is not gated. Fix the test or the skill, then remove the flag.")
        return 0

    if a.cmd == "verify":
        ok, msg = verify_run(a.run)
        print(("OK: " if ok else "FAILED: ") + msg)
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
