#!/usr/bin/env python3
"""Check summary.json numbers against values computed independently from the fixture."""
import json
import sys
from pathlib import Path

out = Path(sys.argv[1] if len(sys.argv) > 1 else "summary.json")
if not out.is_file():
    print(json.dumps({"pass": False, "evidence": "%s missing" % out}))
    sys.exit(0)
s = json.loads(out.read_text())
problems = []
if s.get("rows") != 3:
    problems.append("rows=%r, expected 3" % s.get("rows"))
price = s.get("columns", {}).get("price", {})
for k, want in (("min", 1.5), ("max", 9.0), ("mean", 4.83)):
    if price.get(k) != want:
        problems.append("price.%s=%r, expected %r" % (k, price.get(k), want))
if s.get("columns", {}).get("qty", {}).get("missing") != 1:
    problems.append("qty.missing should be 1")
print(json.dumps({"pass": not problems, "score": 1.0 - len(problems) / 5, "evidence": "; ".join(problems) or "all values correct"}))
