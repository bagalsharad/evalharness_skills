#!/usr/bin/env python3
"""Copy core/skillcheck_core.py into every skill's scripts/ folder.

Each skill must work on its own after install, so each carries its own copy.
Run after editing the core:  python3 tools/sync_core.py   (--check: fail if out of sync)
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "core" / "skillcheck_core.py"
TARGETS = ["skillcheck-lint", "skillcheck-suite", "skillcheck-run", "skillcheck-compare"]


def main() -> int:
    check = "--check" in sys.argv
    src = SRC.read_bytes()
    stale = []
    for name in TARGETS:
        dst = ROOT / "skills" / name / "scripts" / "skillcheck_core.py"
        if not dst.exists() or dst.read_bytes() != src:
            stale.append(dst)
            if not check:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(SRC, dst)
    if check and stale:
        print("out of sync: " + ", ".join(str(p.relative_to(ROOT)) for p in stale))
        print("run: python3 tools/sync_core.py")
        return 1
    print("core in sync (%d skills)" % len(TARGETS) if not stale or not check else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
