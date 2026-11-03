#!/usr/bin/env python3
"""Summarize a CSV file as JSON. Usage: summarize.py INPUT.csv [OUTPUT.json]"""
import csv
import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: summarize.py INPUT.csv [OUTPUT.json]", file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_name("summary.json")
    if not src.is_file():
        print("error: %s not found" % src, file=sys.stderr)
        return 1
    with src.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    cols = {}
    for name in (rows[0].keys() if rows else []):
        vals = [r.get(name, "") for r in rows]
        present = [v for v in vals if v not in ("", None)]
        try:
            nums = [float(v) for v in present]
            cols[name] = {"type": "number", "min": min(nums) if nums else None, "max": max(nums) if nums else None,
                          "mean": round(sum(nums) / len(nums), 2) if nums else None, "missing": len(vals) - len(present)}
        except ValueError:
            cols[name] = {"type": "text", "distinct": len(set(present)), "missing": len(vals) - len(present)}
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps({"rows": len(rows), "columns": cols}, indent=2))
    print("wrote %s" % dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
