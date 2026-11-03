#!/usr/bin/env python3
"""Render skillcheck results as HTML, Markdown and JUnit XML.

  report.py RUN [--compare COMPARISON.json] [--html report.html] [--md report.md] [--junit junit.xml]

RUN is a run directory or results.json. --compare takes JSON written by
`analyze.py compare|gate --out FILE` and adds verdicts and a baseline diff.
The HTML file is self-contained (no network) and includes a trace viewer per trial.
Python 3.9+, stdlib only.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape as xml_escape, quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skillcheck_core as core  # noqa: E402
import analyze  # noqa: E402

CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e3e8;--card:#f7f7f9;--ok:#1a7f37;--bad:#c62828;--warn:#9a6700;--code:#f1f1f4}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececf0;--muted:#9a9aa3;--line:#2c2c31;--card:#1c1c20;--ok:#4cc26a;--bad:#ff6b6b;--warn:#e3b341;--code:#222227}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
.muted{color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:16px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}.card b{display:block;font-size:20px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}.wrap{overflow-x:auto}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.tag{display:inline-block;font-size:11px;padding:0 6px;border:1px solid var(--line);border-radius:999px;margin-right:4px;color:var(--muted)}
details{border:1px solid var(--line);border-radius:8px;margin:6px 0;background:var(--card)}summary{cursor:pointer;padding:8px 10px}
details>div{padding:4px 12px 12px}pre{background:var(--code);padding:8px;border-radius:6px;overflow:auto;max-height:340px;white-space:pre-wrap;word-break:break-word;font-size:12px}
.bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;min-width:80px}.bar>span{display:block;height:100%;background:var(--ok)}
ul.warn{padding-left:18px}
"""


def esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def pct(x: Optional[float]) -> str:
    return "-" if x is None else "%.0f%%" % (100 * x)


def render_html(run: Dict[str, Any], summ: Dict[str, Any], cmp: Optional[Dict[str, Any]]) -> str:
    verdicts = {r["id"]: r for r in (cmp or {}).get("tests", [])}
    H: List[str] = ["<!doctype html><html lang=en><head><meta charset=utf-8>",
                    "<meta name=viewport content='width=device-width,initial-scale=1'>",
                    "<title>skillcheck report</title><style>%s</style></head><body><main>" % CSS]
    H.append("<h1>skillcheck · %s</h1>" % esc(run["suite"]["name"]))
    H.append("<div class=muted>run %s · %s · status %s · models %s · Claude Code %s</div>" % (
        esc(run["run_id"]), esc(run["created_at"]), esc(run["status"]), esc(", ".join(run.get("observed_models") or [])),
        esc(", ".join(run.get("observed_runtime_versions") or []))))
    tot = run["totals"]
    rows = summ["tests"]
    gated = [r for r in rows if r["trials"]]
    mean_rate = sum(r["pass_rate"] for r in gated) / len(gated) if gated else None
    H.append("<div class=cards>")
    for label, val in (("tests", tot["tests"]), ("trials", tot["trials"]), ("mean pass rate", pct(mean_rate)),
                       ("infra errors", tot["errors"]), ("cost", "$%.2f" % tot["cost_usd"])):
        H.append("<div class=card><span class=muted>%s</span><b>%s</b></div>" % (label, esc(val)))
    if cmp:
        sv = cmp["suite"]
        cls = "bad" if sv["verdict"].startswith("regression") else "ok" if sv["verdict"].startswith("improvement") else "warn" if sv["verdict"] == "inconclusive" else ""
        H.append("<div class=card><span class=muted>suite vs baseline</span><b class=%s>%s</b><span class=muted>%+.1f pts</span></div>" % (
            cls, esc(sv["verdict"]), 100 * sv["mean_effect"]))
        if cmp.get("gate"):
            g = cmp["gate"]
            H.append("<div class=card><span class=muted>gate</span><b class=%s>%s</b></div>" % ("bad" if g["blocked"] else "ok", "BLOCKED" if g["blocked"] else "PASSED"))
    H.append("</div>")
    if summ["warnings"] or (cmp and cmp.get("gate")):
        H.append("<ul class=warn>")
        for w in summ["warnings"]:
            H.append("<li class=warn>%s</li>" % esc(w))
        if cmp and cmp.get("gate"):
            for b in cmp["gate"]["blockers"]:
                H.append("<li class=bad>%s</li>" % esc(b))
            for w in cmp["gate"]["warnings"]:
                H.append("<li class=warn>%s</li>" % esc(w))
        H.append("</ul>")
    H.append("<h2>Skills under test</h2><div class=wrap><table><tr><th>name</th><th>content hash</th><th>git</th><th>context footprint (est.)</th></tr>")
    for s in run.get("skills", []):
        fp = s.get("footprint") or {}
        H.append("<tr><td>%s%s</td><td><code>%s</code></td><td>%s%s</td><td>meta %s · body %s · refs %s tok</td></tr>" % (
            esc(s["name"]), " <span class=tag>removed</span>" if s["name"] in (run["target"].get("without_skill") or []) else "",
            esc(s["content_hash"][:23]), esc((s.get("git_sha") or "-")[:10]), " (dirty)" if s.get("dirty") else "",
            fp.get("metadata_tokens_est"), fp.get("body_tokens_est"), fp.get("referenced_resource_tokens_est")))
    H.append("</table></div>")
    if summ["trigger"]:
        H.append("<h2>Triggering</h2><div class=wrap><table><tr><th>skill</th><th>precision</th><th>recall</th><th>tp/fp/tn/fn</th></tr>")
        for sk, d in summ["trigger"].items():
            H.append("<tr><td>%s</td><td>%s %s</td><td>%s %s</td><td>%d/%d/%d/%d</td></tr>" % (
                esc(sk), pct(d["precision"]), esc(analyze.fmt_ci(d["precision_ci"])), pct(d["recall"]),
                esc(analyze.fmt_ci(d["recall_ci"])), d["tp"], d["fp"], d["tn"], d["fn"]))
        H.append("</table></div>")
    H.append("<h2>Tests</h2><div class=wrap><table><tr><th>test</th><th>kind</th><th>pass</th><th></th><th>95%% CI</th>"
             "<th>pass@k / pass^k</th><th>$ / trial</th><th>median s</th>%s<th>flags</th></tr>" % ("<th>vs baseline</th>" if cmp else ""))
    for r in rows:
        flags = []
        for f, on in (("critical", r["critical"]), ("quarantined", r["quarantine"]), ("unreviewed", not r["reviewed"]),
                      ("flaky", r["flaky"]), ("uncalibrated judge", r["uses_judge"] and not r["calibrated"])):
            if on:
                flags.append("<span class=tag>%s</span>" % f)
        v = verdicts.get(r["id"])
        vcell = ""
        if cmp:
            if v:
                cls = {"regression": "bad", "improvement": "ok", "inconclusive": "warn"}.get(v["verdict"], "")
                vcell = "<td class=%s>%s<br><span class=muted>%+.0f pts, q=%.3f</span></td>" % (cls, esc(v["verdict"]), 100 * v["effect"], v["q_value"])
            else:
                vcell = "<td class=muted>new</td>"
        H.append("<tr><td><a href='#t-%s'>%s</a></td><td>%s</td><td>%d/%d</td><td><div class=bar><span style='width:%.0f%%'></span></div></td>"
                 "<td>%s</td><td>%s / %s</td><td>%s</td><td>%s</td>%s<td>%s</td></tr>" % (
                     esc(r["id"]), esc(r["id"]), esc(r["kind"]), r["passed"], r["trials"], 100 * (r["pass_rate"] or 0),
                     esc(analyze.fmt_ci(r["ci"])),
                     "-" if r["pass_at_k"] is None else "%.2f" % r["pass_at_k"],
                     "-" if r["pass_hat_k"] is None else "%.2f" % r["pass_hat_k"],
                     "-" if r["mean_cost_usd"] is None else "%.3f" % r["mean_cost_usd"],
                     "-" if r["median_duration_s"] is None else "%.0f" % r["median_duration_s"], vcell, "".join(flags)))
    H.append("</table></div>")
    H.append("<h2>Trials</h2><p class=muted>Each trial ran in a fresh temporary project. Raw traces are saved next to results.json under trials/.</p>")
    for t in run["tests"]:
        H.append("<h3 id='t-%s' style='font-size:15px;margin:18px 0 4px'>%s <span class=muted>(%s)</span></h3>" % (esc(t["id"]), esc(t["id"]), esc(t["kind"])))
        H.append("<div class=muted>prompt:</div><pre>%s</pre>" % esc(t["prompt"]))
        for tr in t["trials"]:
            st = tr["status"]
            cls = {"passed": "ok", "failed": "bad", "timeout": "bad", "error": "warn"}[st]
            m = tr.get("metrics") or {}
            H.append("<details><summary><b class=%s>%s</b> trial #%d · $%.3f · %ss · %s tool calls · loaded %s</summary><div>" % (
                cls, st.upper(), tr["trial"], m.get("cost_usd") or 0, "%.0f" % ((m.get("duration_ms") or 0) / 1000),
                m.get("tool_calls", "-"), esc(", ".join(tr.get("skills_loaded") or []) or "no skill")))
            if st == "error":
                H.append("<pre>%s</pre></div></details>" % esc(tr.get("error")))
                continue
            H.append("<table><tr><th>grader</th><th>result</th><th>evidence</th></tr>")
            for g in tr.get("grades", []):
                H.append("<tr><td>%s</td><td class=%s>%s %.2f</td><td>%s</td></tr>" % (
                    esc(g["type"]), "ok" if g["passed"] else "bad", "pass" if g["passed"] else "fail", g["score"], esc(g["evidence"])))
            H.append("</table>")
            H.append("<div class=muted>final reply</div><pre>%s</pre>" % esc(tr.get("final_text")))
            files = tr.get("files") or []
            if files:
                H.append("<div class=muted>files in workdir</div><pre>%s</pre>" % esc("\n".join("%s  %d B" % (f["path"], f["bytes"]) for f in files[:50])))
            trace = _load_tool_calls(run, tr)
            if trace:
                H.append("<div class=muted>tool calls</div><pre>%s</pre>" % esc("\n".join(trace)))
            H.append("<div class=muted>model %s · %s turns · %s tokens · terminal %s · trace <code>%s/trace.jsonl</code></div></div></details>" % (
                esc(tr.get("model")), m.get("num_turns"), m.get("total_tokens"), esc(tr.get("terminal_reason")), esc(tr.get("trial_dir"))))
    H.append("<p class=muted style='margin-top:32px'>Pass rates use Wilson 95%% intervals. pass@k: chance that at least one of k "
             "tries passes. pass^k: chance that all k pass. Comparisons: Fisher exact per test with Benjamini-Hochberg "
             "FDR, paired bootstrap at suite level. Generated by skillcheck %s.</p>" % esc(core.CORE_VERSION))
    H.append("</main></body></html>")
    return "\n".join(H)


def _load_tool_calls(run: Dict[str, Any], tr: Dict[str, Any]) -> List[str]:
    src = run.get("_ref")
    if not src or not tr.get("trial_dir"):
        return []
    p = Path(src).parent / tr["trial_dir"] / "summary.json"
    if not p.exists():
        return []
    s = core.load_json(p)
    out = []
    for i, c in enumerate(s.get("tool_calls", [])[:200]):
        arg = c["input"].get("command") or c["input"].get("file_path") or c["input"].get("skill") or json.dumps(c["input"])[:160]
        out.append("%3d. %s%s  %s" % (i + 1, c["name"], " (subagent)" if c.get("subagent") else "", str(arg)[:220]))
    return out


def render_markdown(run: Dict[str, Any], summ: Dict[str, Any], cmp: Optional[Dict[str, Any]]) -> str:
    if cmp:
        return analyze.comparison_markdown(cmp, cmp.get("gate"))
    L = ["## skillcheck: %s" % run["suite"]["name"], "",
         "run `%s` · models %s · %d trials · $%.2f" % (run["run_id"], ", ".join(run.get("observed_models") or []),
                                                    run["totals"]["trials"], run["totals"]["cost_usd"]), "",
         "| test | kind | pass | 95% CI | flags |", "|---|---|---|---|---|"]
    for r in summ["tests"]:
        flags = ", ".join(f for f, on in (("critical", r["critical"]), ("flaky", r["flaky"]), ("unreviewed", not r["reviewed"]),
                                          ("quarantined", r["quarantine"])) if on)
        L.append("| `%s` | %s | %d/%d | %s | %s |" % (r["id"], r["kind"], r["passed"], r["trials"], analyze.fmt_ci(r["ci"]), flags))
    for sk, d in summ["trigger"].items():
        L.append("\nTrigger `%s`: precision %s, recall %s" % (sk, pct(d["precision"]), pct(d["recall"])))
    for w in summ["warnings"]:
        L.append("\n> ⚠️ " + w)
    return "\n".join(L)


def render_junit(run: Dict[str, Any], cmp: Optional[Dict[str, Any]]) -> str:
    cases, failures, errors = [], 0, 0
    for t in run["tests"]:
        for tr in t["trials"]:
            name = "%s#%d" % (t["id"], tr["trial"])
            secs = ((tr.get("metrics") or {}).get("duration_ms") or 0) / 1000
            body = ""
            if tr["status"] == "error":
                errors += 1
                body = "<error message=%s/>" % quoteattr((tr.get("error") or "")[:500])
            elif not tr.get("passed"):
                failures += 1
                msg = "; ".join("%s: %s" % (g["type"], g["evidence"]) for g in tr.get("grades", []) if not g["passed"])
                body = "<failure message=%s/>" % quoteattr(msg[:1000])
            if t.get("quarantine"):
                body = "<skipped message=\"quarantined: trial outcome recorded but not gated\"/>"
            cases.append("  <testcase classname=%s name=%s time=\"%.1f\">%s</testcase>" % (
                quoteattr("skillcheck.%s.%s" % (run["suite"]["name"], t["kind"])), quoteattr(name), secs, body))
    if cmp:
        for r in cmp["tests"]:
            body = ""
            if r["verdict"] == "regression":
                failures += 1
                body = "<failure message=%s/>" % quoteattr("regression %+.0f pts (q=%.3f)" % (100 * r["effect"], r["q_value"]))
            cases.append("  <testcase classname=\"skillcheck.verdict\" name=%s>%s</testcase>" % (quoteattr(r["id"]), body))
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<testsuite name=%s tests=\"%d\" failures=\"%d\" errors=\"%d\">\n%s\n</testsuite>\n"
            % (quoteattr("skillcheck." + run["suite"]["name"]), len(cases), failures, errors, "\n".join(cases)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--compare", help="comparison JSON from analyze.py compare/gate --out")
    ap.add_argument("--html")
    ap.add_argument("--md")
    ap.add_argument("--junit")
    a = ap.parse_args()
    run = analyze.load_run(a.run)
    summ = analyze.summarize(run)
    cmp = core.load_json(Path(a.compare)) if a.compare else None
    if not (a.html or a.md or a.junit):
        a.html = str(Path(run["_ref"]).parent / "report.html")
    if a.html:
        Path(a.html).write_text(render_html(run, summ, cmp), encoding="utf-8")
        print("wrote %s" % a.html)
    if a.md:
        Path(a.md).write_text(render_markdown(run, summ, cmp), encoding="utf-8")
        print("wrote %s" % a.md)
    if a.junit:
        Path(a.junit).write_text(render_junit(run, cmp), encoding="utf-8")
        print("wrote %s" % a.junit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
