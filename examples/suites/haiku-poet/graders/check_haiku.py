#!/usr/bin/env python3
"""Grade a haiku file: 3 non-empty lines, syllables ~5/7/5 (heuristic, +-1 per line).

Usage: check_haiku.py [PATH]   (default haiku.txt). Prints one JSON line.
"""
import json
import re
import sys
from pathlib import Path


def syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and not w.endswith(("le", "ee", "ye")) and n > 1:
        n -= 1
    return max(1, n)


path = Path(sys.argv[1] if len(sys.argv) > 1 else "haiku.txt")
if not path.is_file():
    print(json.dumps({"pass": False, "score": 0, "evidence": "%s missing" % path}))
    sys.exit(0)
lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
if len(lines) != 3:
    print(json.dumps({"pass": False, "score": 0, "evidence": "%d lines, expected 3" % len(lines)}))
    sys.exit(0)
counts = [sum(syllables(w) for w in l.split()) for l in lines]
target = [5, 7, 5]
off = sum(abs(c - t) for c, t in zip(counts, target))
ok = all(abs(c - t) <= 1 for c, t in zip(counts, target))
print(json.dumps({"pass": ok, "score": max(0.0, 1 - off / 6), "evidence": "syllables %s (target 5/7/5)" % counts}))
