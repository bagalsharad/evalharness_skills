#!/usr/bin/env python3
"""Lint Agent Skills (SKILL.md folders) and report their identity and context cost.

  lint_skill.py PATH [PATH ...]        lint one or more skill folders
  lint_skill.py --library DIR          lint every skill under DIR and check
                                       description collisions between them
  options: --json, --strict (warnings fail too), --min-overlap 0.35

Exit code: 0 clean, 1 findings at failing level, 2 usage error.
Python 3.9+, stdlib only (PyYAML used if installed).
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skillcheck_core as core  # noqa: E402

STOP = set("""a an and are as at be by can do for from has have how i if in into is it its of on or
that the their this to use used uses using when whenever with you your user users skill skills
asks ask wants want any all also e.g eg etc such like via""".split())


def words(text: str) -> set:
    return {w for w in re.findall(r"[a-z][a-z0-9+-]{2,}", text.lower()) if w not in STOP}


def overlap(a: str, b: str) -> float:
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def find_skills(root: Path) -> List[Path]:
    return sorted(p.parent for p in root.rglob("SKILL.md")
                  if not any(part in core.IGNORED_PARTS for part in p.parts))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--library", help="directory containing many skills")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
    ap.add_argument("--min-overlap", type=float, default=0.35,
                    help="description word-overlap (Jaccard) that flags a possible collision")
    a = ap.parse_args()
    targets = [Path(p) for p in a.paths]
    if a.library:
        targets += find_skills(Path(a.library))
    if not targets:
        ap.error("give at least one skill folder or --library DIR")

    reports: List[Dict[str, Any]] = []
    for t in targets:
        try:
            sk = core.load_skill(t)
        except FileNotFoundError as e:
            reports.append({"path": str(t), "findings": [{"level": "error", "code": "SC000", "message": str(e)}]})
            continue
        reports.append({"path": sk["path"], "name": sk["name"], "content_hash": sk["content_hash"],
                        "git_sha": sk["git_sha"], "dirty": sk["dirty"], "footprint": sk["footprint"],
                        "files": len(sk["files"]), "description": sk["description"],
                        "findings": core.lint_skill(sk)})

    collisions = []
    named = [r for r in reports if r.get("description")]
    for x, y in itertools.combinations(named, 2):
        if x["name"] == y["name"]:
            collisions.append({"a": x["path"], "b": y["path"], "overlap": 1.0, "reason": "duplicate name"})
            continue
        ov = overlap(x["description"], y["description"])
        if ov >= a.min_overlap:
            shared = sorted(words(x["description"]) & words(y["description"]))[:12]
            collisions.append({"a": x["name"], "b": y["name"], "overlap": round(ov, 2), "shared_terms": shared})

    fail_levels = {"error", "warning"} if a.strict else {"error"}
    failed = any(f["level"] in fail_levels for r in reports for f in r["findings"]) or \
        any(c.get("reason") == "duplicate name" for c in collisions)

    if a.json:
        print(json.dumps({"skills": reports, "collisions": collisions, "failed": failed}, indent=2))
        return 1 if failed else 0

    for r in reports:
        print("\n%s  (%s)" % (r.get("name", "?"), r["path"]))
        if "content_hash" in r:
            fp = r["footprint"]
            print("  hash %s  git %s%s  files %d" % (r["content_hash"][:19], (r["git_sha"] or "-")[:10],
                                                     " (dirty)" if r["dirty"] else "", r["files"]))
            print("  context ≈ metadata %d tok (always loaded) | body %d tok, %d lines (on trigger) | "
                  "referenced files %d tok (on demand)" % (fp["metadata_tokens_est"], fp["body_tokens_est"],
                                                           fp["body_lines"], fp["referenced_resource_tokens_est"]))
        if not r["findings"]:
            print("  ✓ no findings")
        for f in sorted(r["findings"], key=lambda f: ["error", "warning", "info"].index(f["level"])):
            print("  %-7s %s  %s" % (f["level"].upper(), f["code"], f["message"]))
    if collisions:
        print("\nPossible trigger collisions (similar descriptions compete for the same prompts):")
        for c in collisions:
            if c.get("reason"):
                print("  ERROR   %s <-> %s: %s" % (c["a"], c["b"], c["reason"]))
            else:
                print("  WARNING %s <-> %s  overlap %.2f  shared: %s" % (c["a"], c["b"], c["overlap"],
                                                                       ", ".join(c["shared_terms"])))
        print("  -> add interaction tests (skillcheck-suite) with near-miss prompts for these pairs")
    n_err = sum(f["level"] == "error" for r in reports for f in r["findings"])
    n_warn = sum(f["level"] == "warning" for r in reports for f in r["findings"])
    print("\n%d skill(s): %d error(s), %d warning(s)%s" % (len(reports), n_err, n_warn,
                                                         " — FAILED" if failed else ""))
    print("token counts are estimates (~4 chars/token)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
