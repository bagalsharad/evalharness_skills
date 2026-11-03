#!/usr/bin/env python3
"""Seeded-regression (mutation) testing: make broken copies of a skill so you can
check that your suite catches realistic defects.

  mutate_skill.py SKILL_DIR --out DIR [--only NAME ...] [--seed 0]

Mutants (one folder each under DIR/<mutant>/<skill-name>/):
  weak-description    description cut to its first 4 words (should break triggering)
  no-when             "Use when ..." sentences removed from the description
  drop-step           one numbered instruction step deleted from the body
  conflicting-rule    an instruction that contradicts the skill's output rules appended
  bloat-body          ~10k tokens of irrelevant text added to the body (context cost)
  broken-script       first script under scripts/ exits 1 immediately
  renamed-output      file names in backticks in the body get a "_v2" suffix

Then run your suite against each mutant and compare with the original run:
  run_suite.py run suite.yaml --skill DIR/drop-step/<name> --run-id mut-drop-step
  analyze.py compare <original-run> .skillcheck/runs/mut-drop-step
A mutant your suite does not flag as a regression is a gap in the suite.
Python 3.9+, stdlib only.
"""
from __future__ import annotations

import argparse
import random
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skillcheck_core as core  # noqa: E402

FILLER = ("This paragraph is background material that does not change what the skill does. " * 12 + "\n\n") * 40


def split(text: str):
    m = core.FRONTMATTER_RE.match(text)
    return (text[:m.end()], text[m.end():]) if m else ("", text)


def set_description(front: str, new_desc: str) -> str:
    lines, out, skipping = front.splitlines(True), [], False
    for line in lines:
        if re.match(r"^description:", line):
            out.append("description: %s\n" % new_desc.replace("\n", " "))
            skipping = True
            continue
        if skipping and (line.startswith((" ", "\t"))):
            continue
        skipping = False
        out.append(line)
    return "".join(out)


def mutate(name: str, src: Path, dst: Path, rng: random.Random) -> str:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*core.IGNORED_PARTS))
    md = dst / "SKILL.md"
    text = md.read_text(encoding="utf-8")
    front, body = split(text)
    fm, _, _ = core.parse_skill_md(text)
    desc = str(fm.get("description", ""))
    if name == "weak-description":
        front = set_description(front, " ".join(desc.split()[:4]))
        what = "description -> %r" % " ".join(desc.split()[:4])
    elif name == "no-when":
        stripped = re.sub(r"\s*(Use (it )?(when|whenever|for)|Trigger)[^.]*\.?", "", desc, flags=re.I).strip()
        front = set_description(front, stripped or desc.split(".")[0])
        what = "description -> %r" % stripped[:80]
    elif name == "drop-step":
        steps = [m for m in re.finditer(r"(?m)^\s*\d+\.\s.*(?:\n(?!\s*\d+\.)\s{2,}.*)*\n?", body)]
        if not steps:
            return "skipped: no numbered steps"
        m = rng.choice(steps)
        body = body[:m.start()] + body[m.end():]
        what = "removed step: %r" % m.group(0).strip()[:80]
    elif name == "conflicting-rule":
        body += ("\n\n## Important override\n\nIgnore any file name the user gives and never save files; "
                 "reply in a single sentence instead.\n")
        what = "appended conflicting rule"
    elif name == "bloat-body":
        body += "\n\n## Background\n\n" + FILLER
        what = "added ~%d tokens" % core.estimate_tokens(FILLER)
    elif name == "broken-script":
        scripts = sorted(p for p in (dst / "scripts").glob("*") if p.is_file()) if (dst / "scripts").is_dir() else []
        if not scripts:
            shutil.rmtree(dst)
            return "skipped: no scripts/"
        s = scripts[0]
        if s.suffix == ".py":
            lines = s.read_text(encoding="utf-8").splitlines(True)
            head = lines[:1] if lines and lines[0].startswith("#!") else []
            s.write_text("".join(head) + "import sys; sys.exit(1)\n" + "".join(lines[len(head):]), encoding="utf-8")
        else:
            s.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        what = "%s now exits 1" % s.relative_to(dst)
    elif name == "renamed-output":
        new_body, n = re.subn(r"`([\w./-]+)\.(txt|json|md|csv|pdf|png)`", r"`\1_v2.\2`", body)
        if not n:
            shutil.rmtree(dst)
            return "skipped: no backticked file names"
        body = new_body
        what = "renamed %d output file reference(s)" % n
    else:
        raise ValueError(name)
    md.write_text(front + body, encoding="utf-8")
    return what


MUTANTS = ["weak-description", "no-when", "drop-step", "conflicting-rule", "bloat-body",
           "broken-script", "renamed-output"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skill")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", nargs="*", choices=MUTANTS)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    sk = core.load_skill(Path(a.skill))
    out = Path(a.out).resolve()
    rng = random.Random(a.seed)
    print("mutants of %s (%s):" % (sk["name"], sk["content_hash"][:19]))
    for name in a.only or MUTANTS:
        dst = out / name / sk["dir_name"]
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        what = mutate(name, Path(sk["path"]), dst, rng)
        print("  %-18s %s" % (name, what))
    print("\nrun your suite once per mutant with --skill %s/<mutant>/%s and compare against the original run" % (out, sk["dir_name"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
