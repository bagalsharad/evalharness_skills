#!/usr/bin/env bash
# Install the skillcheck skills for Claude Code.
#   ./install.sh                 personal install: ~/.claude/skills (all your projects)
#   ./install.sh --project DIR   project install: DIR/.claude/skills (commit it to share with your team)
#   ./install.sh --link          symlink instead of copy (for working on skillcheck itself)
#   ./install.sh --uninstall     remove the skillcheck skills from the target
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skills"
DEST="$HOME/.claude/skills"
MODE=copy
ACTION=install
while [ $# -gt 0 ]; do
  case "$1" in
    --project) DEST="$(cd "${2:?--project needs a directory}" && pwd)/.claude/skills"; shift 2 ;;
    --link) MODE=link; shift ;;
    --uninstall) ACTION=uninstall; shift ;;
    -h|--help) sed -n 2,7p "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
SKILLS="skillcheck skillcheck-lint skillcheck-suite skillcheck-run skillcheck-compare"

if [ "$ACTION" = uninstall ]; then
  for s in $SKILLS; do rm -rf "${DEST:?}/$s" && echo "removed $DEST/$s"; done
  exit 0
fi

python3 - <<'PY' || { echo "skillcheck needs Python 3.9+ as python3" >&2; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY
mkdir -p "$DEST"
for s in $SKILLS; do
  target="$DEST/$s"
  if [ -e "$target" ] || [ -L "$target" ]; then
    if [ -f "$target/SKILL.md" ] && ! grep -q "^name: $s\$" "$target/SKILL.md"; then
      echo "refusing to overwrite $target: it is not a skillcheck skill" >&2; exit 1
    fi
    rm -rf "$target"
  fi
  if [ "$MODE" = link ]; then ln -s "$SRC/$s" "$target"; else cp -R "$SRC/$s" "$target"; fi
  chmod +x "$target"/scripts/*.py 2>/dev/null || true
  echo "installed $s -> $target"
done

echo
python3 -c "import yaml" 2>/dev/null && echo "PyYAML: ok" || \
  echo "PyYAML: missing. YAML suites need it: pip install pyyaml (or run scripts with 'uv run')"
if command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ]; then
  echo "claude CLI: ok"
else
  echo "claude CLI: not found. Install Claude Code, or set SKILLCHECK_CLAUDE_BIN to its path"
fi
echo
echo "Start a new Claude Code session and ask: \"use skillcheck to test my skill at <path>\""
echo "or run: python3 $DEST/skillcheck-run/scripts/run_suite.py doctor"
