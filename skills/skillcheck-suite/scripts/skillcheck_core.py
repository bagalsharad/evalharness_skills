"""skillcheck_core: shared, dependency-free library for the skillcheck skills.

SOURCE OF TRUTH lives in core/skillcheck_core.py. Every skill ships an identical
copy in its scripts/ folder so each skill works on its own after install.
Run `python3 tools/sync_core.py` after editing; tests fail if copies drift.

Contents:
  1. SKILL.md parsing, canonical content hashing, context-footprint estimates
  2. Lint rules (Agent Skills spec, https://agentskills.io/specification)
  3. Test-suite loading: YAML/JSON, `extends`, matrix expansion, validation
  4. Statistics: Wilson, Newcombe, Fisher exact, exact McNemar, pass@k,
     pass^k, Benjamini-Hochberg, paired bootstrap, minimum detectable effect
  5. Hash chain helpers for tamper-evident run logs

Python 3.9+, stdlib only. PyYAML is used when installed (needed for .yaml suites).
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import random
import re
import subprocess
from pathlib import Path
from statistics import NormalDist
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

CORE_VERSION = "0.1.0"
SPEC_VERSION = 1          # test-suite format version
SCHEMA_VERSION = 1        # results JSON version

try:  # optional
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


# ---------------------------------------------------------------------------
# 1. Skill parsing and hashing
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
IGNORED_PARTS = {".git", "__pycache__", ".DS_Store", "node_modules", ".venv", ".pytest_cache"}


def _mini_yaml(text: str) -> Dict[str, Any]:
    """Tiny fallback for frontmatter when PyYAML is missing.

    Handles `key: value`, quoted values, `key: >`/`|` blocks and one level of
    nested `key:` maps (enough for name/description/metadata).
    """
    out: Dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2).strip()
        if val in (">", "|", ">-", "|-", ">+", "|+"):
            block: List[str] = []
            i += 1
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            out[key] = (" " if val.startswith(">") else "\n").join(block).strip()
            continue
        if val == "":
            sub: Dict[str, str] = {}
            i += 1
            while i < len(lines) and lines[i].startswith((" ", "\t")):
                sm = re.match(r"^\s+([A-Za-z0-9_-]+):\s*(.*)$", lines[i])
                if sm:
                    sub[sm.group(1)] = sm.group(2).strip().strip("'\"")
                i += 1
            out[key] = sub
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        out[key] = val
        i += 1
    return out


def parse_skill_md(text: str) -> Tuple[Dict[str, Any], str, Optional[str]]:
    """Return (frontmatter, body, error)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text, "SKILL.md must start with YAML frontmatter delimited by '---' lines"
    raw = m.group(1)
    body = text[m.end():]
    try:
        if yaml is not None:
            fm = yaml.safe_load(raw) or {}
        else:
            fm = _mini_yaml(raw)
    except Exception as e:  # yaml.YAMLError
        return {}, body, "frontmatter is not valid YAML: %s" % e
    if not isinstance(fm, dict):
        return {}, body, "frontmatter must be a YAML mapping"
    return fm, body, None


def iter_skill_files(root: Path) -> List[Path]:
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and not any(part in IGNORED_PARTS for part in p.relative_to(root).parts):
            files.append(p)
    return files


def content_hash(root: Path) -> str:
    """Canonical hash of a skill folder: sorted POSIX paths + LF-normalized text.

    Binary files are hashed as-is. Identity is independent of mtime, OS and
    line-ending style, so the same skill hashes the same everywhere.
    """
    h = hashlib.sha256()
    for p in iter_skill_files(root):
        rel = p.relative_to(root).as_posix()
        data = p.read_bytes()
        if b"\0" not in data[:8192]:
            data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        h.update(rel.encode("utf-8") + b"\0")
        h.update(hashlib.sha256(data).digest())
    return "sha256:" + h.hexdigest()


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_info(path: Path) -> Dict[str, Any]:
    """Git SHA and dirty flag for the repo containing `path` (if any)."""
    try:
        sha = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        if sha.returncode != 0:
            return {"git_sha": None, "dirty": None}
        st = subprocess.run(["git", "-C", str(path), "status", "--porcelain", "--", "."],
                            capture_output=True, text=True, timeout=10)
        return {"git_sha": sha.stdout.strip(), "dirty": bool(st.stdout.strip())}
    except Exception:
        return {"git_sha": None, "dirty": None}


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token for English prose and code).

    No tokenizer ships offline; treat these as estimates, not billing numbers.
    """
    return int(math.ceil(len(text) / 4.0)) if text else 0


LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
PATH_HINT_RE = re.compile(r"(?<![\w/.-])((?:scripts|references|assets)/[\w./-]+[\w])")


def referenced_paths(body: str) -> List[str]:
    refs = set()
    for m in LINK_RE.finditer(body):
        target = m.group(1).split("#")[0]
        if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("/"):
            continue
        refs.add(target)
    for m in PATH_HINT_RE.finditer(body):
        refs.add(m.group(1))
    return sorted(refs)


def load_skill(path: Path) -> Dict[str, Any]:
    """Load a skill folder (or a SKILL.md path) into a plain dict."""
    path = Path(path).expanduser().resolve()
    root = path.parent if path.name == "SKILL.md" else path
    md = root / "SKILL.md"
    if not md.is_file():
        raise FileNotFoundError("no SKILL.md in %s" % root)
    text = md.read_text(encoding="utf-8")
    fm, body, err = parse_skill_md(text)
    files = [p.relative_to(root).as_posix() for p in iter_skill_files(root)]
    refs = referenced_paths(body)
    resource_tokens = 0
    for r in refs:
        rp = (root / r)
        if rp.is_file():
            try:
                resource_tokens += estimate_tokens(rp.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                pass
    meta_text = "name: %s\ndescription: %s" % (fm.get("name", ""), fm.get("description", ""))
    return {
        "path": str(root),
        "dir_name": root.name,
        "name": fm.get("name") or root.name,
        "description": fm.get("description") or "",
        "frontmatter": fm,
        "frontmatter_error": err,
        "body": body,
        "files": files,
        "references": refs,
        "content_hash": content_hash(root),
        **git_info(root),
        "footprint": {
            "metadata_tokens_est": estimate_tokens(meta_text),
            "body_tokens_est": estimate_tokens(body),
            "body_lines": body.count("\n") + 1,
            "referenced_resource_tokens_est": resource_tokens,
        },
    }


def skill_identity(skill: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("name", "path", "content_hash", "git_sha", "dirty", "footprint")
    return {k: skill.get(k) for k in keys}


# ---------------------------------------------------------------------------
# 2. Lint
# ---------------------------------------------------------------------------

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SPEC_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
# Extra keys Claude Code understands (code.claude.com/docs/en/skills, read 2026-11-02).
# Outside Claude Code (claude.ai upload, Skills API) these are a hard error.
CLAUDE_CODE_KEYS = {"when_to_use", "argument-hint", "arguments", "disable-model-invocation",
                    "user-invocable", "disallowed-tools", "model", "effort", "context", "agent",
                    "background", "hooks", "paths", "shell"}
RESERVED_DIR_NAMES = {"synced", "anthropic-skills"}
INTERPRETERS = {".py": "python", ".sh": "sh/bash", ".bash": "bash", ".js": "node",
                ".mjs": "node", ".ts": "ts-node/deno/bun", ".rb": "ruby", ".pl": "perl"}


def lint_skill(skill: Dict[str, Any], max_body_lines: int = 500,
               max_body_tokens: int = 5000, min_description_chars: int = 40) -> List[Dict[str, str]]:
    """Return findings: [{level: error|warning|info, code, message}]."""
    out: List[Dict[str, str]] = []

    def add(level: str, code: str, msg: str) -> None:
        out.append({"level": level, "code": code, "message": msg})

    if skill.get("frontmatter_error"):
        add("error", "SC001", skill["frontmatter_error"])
        return out
    fm = skill["frontmatter"]
    root = Path(skill["path"])

    name = fm.get("name")
    if not name:
        add("error", "SC002", "frontmatter `name` is required")
    else:
        name = str(name)
        if len(name) > 64:
            add("error", "SC003", "`name` is %d chars; max is 64" % len(name))
        if not NAME_RE.match(name):
            add("error", "SC004", "`name` %r must be lowercase a-z, 0-9 and single hyphens, "
                "not starting or ending with a hyphen" % name)
        if name != skill["dir_name"]:
            add("error", "SC005", "`name` %r must match the folder name %r" % (name, skill["dir_name"]))

    desc = fm.get("description")
    if not desc or not str(desc).strip():
        add("error", "SC006", "frontmatter `description` is required and must be non-empty")
    else:
        desc = str(desc).strip()
        if len(desc) > 1024:
            add("error", "SC007", "`description` is %d chars; max is 1024" % len(desc))
        if len(desc) < min_description_chars:
            add("warning", "SC008", "`description` is only %d chars; say what the skill does AND "
                "when to use it, with trigger keywords" % len(desc))
        if not re.search(r"\b(use|when|whenever|trigger|if the user)\b", desc, re.I):
            add("warning", "SC009", "`description` never says when to use the skill "
                "(e.g. 'Use when ...'); triggering will be unreliable")
        if re.search(r"<[a-z/][^>]*>", desc):
            add("warning", "SC010", "`description` contains XML/HTML-like tags; some clients reject them")

    comp = fm.get("compatibility")
    if comp is not None and (not str(comp).strip() or len(str(comp)) > 500):
        add("error", "SC011", "`compatibility` must be 1-500 chars when present")
    meta = fm.get("metadata")
    if meta is not None:
        if not isinstance(meta, dict):
            add("error", "SC012", "`metadata` must be a map of string keys to string values")
        else:
            bad = [k for k, v in meta.items() if not isinstance(v, str)]
            if bad:
                add("warning", "SC013", "`metadata` values should be strings (quote them): %s" % ", ".join(map(str, bad)))
    at = fm.get("allowed-tools")
    if at is not None and not isinstance(at, (str, list)):
        add("error", "SC014", "`allowed-tools` must be a space-separated string")
    unknown = sorted(set(fm) - SPEC_KEYS - CLAUDE_CODE_KEYS)
    if unknown:
        add("warning", "SC015", "frontmatter keys not in the Agent Skills spec or Claude Code docs "
            "(Claude Code silently ignores them): %s" % ", ".join(unknown))
    cc_only = sorted(set(fm) & CLAUDE_CODE_KEYS)
    if cc_only:
        add("info", "SC026", "Claude Code-only keys %s: fine in Claude Code, but claude.ai uploads "
            "and the Skills API reject them" % ", ".join(cc_only))
    if skill["dir_name"] in RESERVED_DIR_NAMES:
        add("error", "SC027", "folder name %r is reserved by Claude Code" % skill["dir_name"])
    listing = len(str(fm.get("description") or "")) + len(str(fm.get("when_to_use") or ""))
    if listing > 1536:
        add("warning", "SC028", "description + when_to_use is %d chars; Claude Code truncates the "
            "listing at 1536, so put the key use case first" % listing)

    fp = skill["footprint"]
    if fp["body_lines"] > max_body_lines:
        add("warning", "SC016", "SKILL.md body is %d lines; keep it under %d and move detail into "
            "references/" % (fp["body_lines"], max_body_lines))
    if fp["body_tokens_est"] > max_body_tokens:
        add("warning", "SC017", "SKILL.md body is ~%d tokens; recommended under %d"
            % (fp["body_tokens_est"], max_body_tokens))
    if not skill["body"].strip():
        add("warning", "SC018", "SKILL.md body is empty")

    for ref in skill["references"]:
        target = (root / ref)
        if not target.exists():
            add("error", "SC019", "broken relative reference: %s" % ref)
        elif len(Path(ref).parts) > 2 and Path(ref).parts[0] not in ("scripts", "references", "assets"):
            add("info", "SC020", "deep reference %s; keep references one level deep" % ref)
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError:
            add("error", "SC021", "reference escapes the skill folder: %s" % ref)

    for rel in skill["files"]:
        p = root / rel
        ext = p.suffix.lower()
        if not rel.startswith("scripts/") or ext not in INTERPRETERS:
            continue
        try:
            src = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            src = ""
        first = src.splitlines()[:1]
        if ext == ".py" and not re.search(r"(?m)^if\s+__name__\s*==\s*['\"]__main__['\"]", src):
            continue  # an importable module, not an entry point
        has_shebang = bool(first and first[0].startswith("#!"))
        mentioned = re.search(r"\b(python3?|uv run|bash|sh|node|deno|bun|ruby|perl)\b[^\n]*"
                              + re.escape(Path(rel).name), skill["body"])
        if not has_shebang and not mentioned:
            add("warning", "SC022", "%s has no shebang and SKILL.md never says how to run it "
                "(expected %s)" % (rel, INTERPRETERS[ext]))
        if has_shebang and not os.access(p, os.X_OK) and not mentioned:
            add("info", "SC023", "%s has a shebang but is not executable (chmod +x)" % rel)

    for rel in skill["files"]:
        size = (root / rel).stat().st_size
        if size > 5_000_000:
            add("warning", "SC024", "%s is %.1f MB; large files slow installs" % (rel, size / 1e6))
    secret_re = re.compile(r"(sk-ant-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|"
                           r"-----BEGIN [A-Z ]*PRIVATE KEY-----)")
    for rel in skill["files"]:
        try:
            if secret_re.search((root / rel).read_text(encoding="utf-8")):
                add("error", "SC025", "%s appears to contain a secret or private key" % rel)
        except (UnicodeDecodeError, OSError):
            pass
    return out


# ---------------------------------------------------------------------------
# 3. Test suites
# ---------------------------------------------------------------------------

TEST_KINDS = ("trigger", "behavior", "adversarial", "interaction", "budget")
GRADER_TYPES = ("file_exists", "file_absent", "file_contains", "file_hash", "output_contains",
                "output_regex", "json_schema", "command", "python", "trace", "budget", "llm_rubric")
TEST_KEYS = {"id", "kind", "prompt", "description", "tags", "fixtures", "graders", "trials",
             "timeout_s", "expect_trigger", "skill", "skills", "expect_skill", "exclusive",
             "budget", "critical", "quarantine", "quarantine_reason", "reviewed", "matrix",
             "allow_tools", "tools", "model", "max_turns", "setup", "notes"}
SUITE_KEYS = {"spec_version", "suite", "description", "extends", "skills", "defaults", "tests",
              "distractors"}
DEFAULT_SETTINGS = {"trials": 5, "timeout_s": 300, "max_turns": 30}


class SuiteError(ValueError):
    pass


def _read_structured(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    if yaml is None:
        raise SuiteError("%s is YAML but PyYAML is not installed. Install it (pip install pyyaml), "
                         "run with `uv run`, or write the suite as JSON." % path)
    return yaml.safe_load(text)


def _render(value: Any, params: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        def sub(m: "re.Match[str]") -> str:
            k = m.group(1).strip()
            if k not in params:
                raise SuiteError("unknown template variable {{%s}}" % k)
            return str(params[k])
        return re.sub(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", sub, value)
    if isinstance(value, list):
        return [_render(v, params) for v in value]
    if isinstance(value, dict):
        return {k: _render(v, params) for k, v in value.items()}
    return value


def _expand_matrix(test: Dict[str, Any]) -> List[Dict[str, Any]]:
    matrix = test.get("matrix")
    if not matrix:
        return [test]
    if isinstance(matrix, dict):
        keys = sorted(matrix)
        combos = [dict(zip(keys, vals)) for vals in itertools.product(*(matrix[k] for k in keys))]
    elif isinstance(matrix, list):
        combos = matrix
    else:
        raise SuiteError("test %s: matrix must be a map of lists or a list of maps" % test.get("id"))
    out = []
    base = {k: v for k, v in test.items() if k != "matrix"}
    for i, params in enumerate(combos):
        t = _render(base, params)
        t["id"] = "%s[%d]" % (test["id"], i)
        t["params"] = params
        t["parent_id"] = test["id"]
        out.append(t)
    return out


def load_suite(path: Path, _seen: Optional[set] = None) -> Dict[str, Any]:
    """Load a suite, resolving `extends` (child tests override parent tests by id)."""
    path = Path(path).expanduser().resolve()
    _seen = _seen or set()
    if path in _seen:
        raise SuiteError("circular `extends` at %s" % path)
    _seen.add(path)
    data = _read_structured(path)
    if not isinstance(data, dict):
        raise SuiteError("%s: suite must be a mapping" % path)
    for t in data.get("tests") or []:
        if isinstance(t, dict):
            t.setdefault("_base_dir", str(path.parent))
    if data.get("extends"):
        parent = load_suite((path.parent / data["extends"]), _seen)
        merged_defaults = dict(parent.get("defaults") or {})
        merged_defaults.update(data.get("defaults") or {})
        tests = {t["id"]: t for t in parent.get("tests") or []}
        for t in data.get("tests") or []:
            tests[t["id"]] = t
        skills = data.get("skills", parent.get("skills"))
        distractors = data.get("distractors", parent.get("distractors"))
        data = {**parent, **data, "defaults": merged_defaults, "tests": list(tests.values()),
                "skills": skills, "distractors": distractors}
    data["_path"] = str(path)
    data["_dir"] = str(path.parent)
    return data


def validate_suite(suite: Dict[str, Any]) -> List[str]:
    """Return a list of human-readable errors (empty means valid)."""
    errs: List[str] = []
    if suite.get("spec_version") != SPEC_VERSION:
        errs.append("spec_version must be %d (got %r)" % (SPEC_VERSION, suite.get("spec_version")))
    if not suite.get("suite"):
        errs.append("`suite` (name) is required")
    for k in suite:
        if not k.startswith("_") and k not in SUITE_KEYS:
            errs.append("unknown suite key %r" % k)
    tests = suite.get("tests")
    if not isinstance(tests, list) or not tests:
        errs.append("`tests` must be a non-empty list")
        return errs
    seen = set()
    for i, t in enumerate(tests):
        where = "tests[%d]" % i
        if not isinstance(t, dict):
            errs.append("%s must be a mapping" % where)
            continue
        tid = t.get("id")
        if not tid or not re.match(r"^[A-Za-z0-9_.-]+$", str(tid)):
            errs.append("%s: `id` is required (letters, digits, _ . -)" % where)
        where = "test %s" % tid
        if tid in seen:
            errs.append("%s: duplicate id" % where)
        seen.add(tid)
        for k in t:
            if not k.startswith("_") and k not in TEST_KEYS:
                errs.append("%s: unknown key %r" % (where, k))
        kind = t.get("kind")
        if kind not in TEST_KINDS:
            errs.append("%s: kind must be one of %s" % (where, ", ".join(TEST_KINDS)))
        if not isinstance(t.get("prompt"), str) or not t["prompt"].strip():
            errs.append("%s: `prompt` is required" % where)
        if kind == "trigger" and not isinstance(t.get("expect_trigger"), bool):
            errs.append("%s: trigger tests need `expect_trigger: true|false`" % where)
        if kind == "interaction" and not t.get("expect_skill") and t.get("expect_skill") is not None:
            errs.append("%s: `expect_skill` must be a skill name or null" % where)
        if kind == "interaction" and "expect_skill" not in t:
            errs.append("%s: interaction tests need `expect_skill` (a name, or null for none)" % where)
        if kind in ("behavior", "adversarial") and not t.get("graders"):
            errs.append("%s: %s tests need at least one grader" % (where, kind))
        if kind == "budget" and not t.get("budget"):
            errs.append("%s: budget tests need a `budget` block" % where)
        for j, g in enumerate(t.get("graders") or []):
            if not isinstance(g, dict) or g.get("type") not in GRADER_TYPES:
                errs.append("%s: graders[%d].type must be one of %s" % (where, j, ", ".join(GRADER_TYPES)))
                continue
            need = {"file_exists": ["path"], "file_absent": ["path"], "file_contains": ["path"],
                    "file_hash": ["path", "sha256"], "output_contains": ["text"],
                    "output_regex": ["pattern"], "json_schema": ["path", "schema"],
                    "command": ["run"], "python": ["entrypoint"], "llm_rubric": ["rubric"]}
            for key in need.get(g["type"], []):
                if key not in g:
                    errs.append("%s: graders[%d] (%s) needs `%s`" % (where, j, g["type"], key))
            if g["type"] == "file_contains" and not ({"text", "pattern"} & set(g)):
                errs.append("%s: graders[%d] file_contains needs `text` or `pattern`" % (where, j))
        if t.get("trials") is not None and (not isinstance(t["trials"], int) or t["trials"] < 1):
            errs.append("%s: trials must be a positive integer" % where)
        base = Path(t.get("_base_dir") or suite.get("_dir") or ".")
        for fx in t.get("fixtures") or []:
            src = fx if isinstance(fx, str) else (fx or {}).get("src")
            if src and "{{" not in src and not (base / src).exists():
                errs.append("%s: fixture not found: %s" % (where, src))
    return errs


def expand_tests(suite: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Apply defaults and matrix expansion. Returns concrete tests."""
    defaults = dict(DEFAULT_SETTINGS)
    defaults.update(suite.get("defaults") or {})
    out = []
    for t in suite["tests"]:
        for c in _expand_matrix(dict(t)):
            c.setdefault("trials", defaults.get("trials"))
            c.setdefault("timeout_s", defaults.get("timeout_s"))
            c.setdefault("max_turns", defaults.get("max_turns"))
            for k in ("model", "allow_tools", "tools", "budget"):
                if k not in c and k in defaults:
                    c[k] = defaults[k]
            c.setdefault("tags", [])
            c.setdefault("critical", False)
            c.setdefault("quarantine", False)
            c.setdefault("reviewed", True)
            out.append(c)
    return out


def filter_tests(tests: List[Dict[str, Any]], tags: Sequence[str] = (), exclude_tags: Sequence[str] = (),
                 ids: Sequence[str] = (), kinds: Sequence[str] = ()) -> List[Dict[str, Any]]:
    res = []
    for t in tests:
        if tags and not set(tags) & set(t.get("tags") or []):
            continue
        if exclude_tags and set(exclude_tags) & set(t.get("tags") or []):
            continue
        if ids and t["id"] not in ids and t.get("parent_id") not in ids:
            continue
        if kinds and t["kind"] not in kinds:
            continue
        res.append(t)
    return res


# ---------------------------------------------------------------------------
# 4. Statistics (see skillcheck-compare/references/statistics.md)
# ---------------------------------------------------------------------------

_N = NormalDist()


def z_for(alpha_two_sided: float) -> float:
    return _N.inv_cdf(1 - alpha_two_sided / 2)


def wilson_interval(k: int, n: int, alpha: float = 0.05) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    z = z_for(alpha)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def newcombe_diff_interval(k1: int, n1: int, k2: int, n2: int, alpha: float = 0.05) -> Tuple[float, float]:
    """Newcombe (1998, method 10) hybrid score CI for p2 - p1 (independent samples)."""
    if n1 == 0 or n2 == 0:
        return (-1.0, 1.0)
    p1, p2 = k1 / n1, k2 / n2
    l1, u1 = wilson_interval(k1, n1, alpha)
    l2, u2 = wilson_interval(k2, n2, alpha)
    d = p2 - p1
    lo = d - math.sqrt((p2 - l2) ** 2 + (u1 - p1) ** 2)
    hi = d + math.sqrt((u2 - p2) ** 2 + (p1 - l1) ** 2)
    return (max(-1.0, lo), min(1.0, hi))


def _log_comb(n: int, k: int) -> float:
    if k < 0 or k > n:
        return float("-inf")
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_exact_two_sided(k1: int, n1: int, k2: int, n2: int) -> float:
    """Two-sided Fisher exact test on the 2x2 table [[k1, n1-k1], [k2, n2-k2]].

    Sums probabilities of all tables at most as likely as the observed one
    (same convention as R's fisher.test and scipy.stats.fisher_exact).
    """
    total_k, total = k1 + k2, n1 + n2
    lo, hi = max(0, total_k - n2), min(total_k, n1)
    denom = _log_comb(total, total_k)

    def logp(x: int) -> float:
        return _log_comb(n1, x) + _log_comb(n2, total_k - x) - denom

    obs = logp(k1)
    p = 0.0
    for x in range(lo, hi + 1):
        lp = logp(x)
        if lp <= obs + 1e-7:
            p += math.exp(lp)
    return min(1.0, p)


def binom_test_two_sided(k: int, n: int, p: float = 0.5) -> float:
    """Exact two-sided binomial test (minimum-likelihood method)."""
    if n == 0:
        return 1.0

    def logpmf(x: int) -> float:
        if p in (0.0, 1.0):
            return 0.0 if (x == 0 and p == 0.0) or (x == n and p == 1.0) else float("-inf")
        return _log_comb(n, x) + x * math.log(p) + (n - x) * math.log(1 - p)

    obs = logpmf(k)
    return min(1.0, sum(math.exp(logpmf(x)) for x in range(n + 1) if logpmf(x) <= obs + 1e-7))


def mcnemar_exact(b: int, c: int) -> float:
    """Exact McNemar test: b = pass->fail pairs, c = fail->pass pairs."""
    return binom_test_two_sided(b, b + c, 0.5)


def pass_at_k(n: int, c: int, k: int) -> Optional[float]:
    """Unbiased pass@k (Chen et al. 2021): P(at least one of k sampled trials passes)."""
    if k > n or n == 0:
        return None
    if n - c < k:
        return 1.0
    return 1.0 - math.exp(_log_comb(n - c, k) - _log_comb(n, k))


def pass_hat_k(n: int, c: int, k: int) -> Optional[float]:
    """pass^k (Yao et al. 2024, tau-bench): P(all k sampled trials pass), unbiased."""
    if k > n or n == 0:
        return None
    if c < k:
        return 0.0
    return math.exp(_log_comb(c, k) - _log_comb(n, k))


def benjamini_hochberg(pvalues: Sequence[float]) -> List[float]:
    """BH-adjusted q-values, same order as input."""
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, pvalues[i] * m / rank)
        q[i] = min(1.0, running)
    return q


def paired_bootstrap_ci(diffs: Sequence[float], alpha: float = 0.05, iters: int = 10000,
                        seed: int = 0) -> Tuple[float, float, float]:
    """Percentile bootstrap CI of the mean of paired differences. Returns (mean, lo, hi)."""
    n = len(diffs)
    if n == 0:
        return (0.0, 0.0, 0.0)
    mean = sum(diffs) / n
    if n == 1:
        return (mean, mean, mean)
    rng = random.Random(seed)
    stats = []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            s += diffs[rng.randrange(n)]
        stats.append(s / n)
    stats.sort()
    lo = stats[int(math.floor((alpha / 2) * iters))]
    hi = stats[min(iters - 1, int(math.ceil((1 - alpha / 2) * iters)) - 1)]
    return (mean, lo, hi)


def paired_bootstrap_pvalue(diffs: Sequence[float], iters: int = 10000, seed: int = 0) -> float:
    """Two-sided bootstrap p-value for mean(diffs) == 0 (shift-to-null method)."""
    n = len(diffs)
    if n < 2:
        return 1.0
    mean = sum(diffs) / n
    centred = [d - mean for d in diffs]
    if all(abs(d) < 1e-12 for d in centred):
        return 1.0 if abs(mean) < 1e-12 else 0.0
    rng = random.Random(seed)
    extreme = 0
    for _ in range(iters):
        s = sum(centred[rng.randrange(n)] for _ in range(n)) / n
        if abs(s) >= abs(mean) - 1e-12:
            extreme += 1
    return (extreme + 1) / (iters + 1)


def min_detectable_effect(p_base: float, n_base: int, n_cand: int, alpha: float = 0.05,
                          power: float = 0.8) -> float:
    """Approximate smallest drop in pass rate a two-proportion test can detect.

    Normal approximation with a Laplace-smoothed variance so p=1.0 does not
    claim infinite sensitivity. Conservative for small n; advisory only.
    """
    if n_base == 0 or n_cand == 0:
        return 1.0
    p = (p_base * n_base + 1) / (n_base + 2)
    se = math.sqrt(p * (1 - p) * (1 / n_base + 1 / n_cand))
    return min(1.0, (z_for(alpha) + _N.inv_cdf(power)) * se)


def trials_needed(p_base: float, effect: float, alpha: float = 0.05, power: float = 0.8,
                  max_n: int = 2000) -> Optional[int]:
    """Trials per side (equal n) so that min_detectable_effect <= effect."""
    for n in range(2, max_n + 1):
        if min_detectable_effect(p_base, n, n, alpha, power) <= effect:
            return n
    return None


def cohens_kappa(a: Sequence[bool], b: Sequence[bool]) -> Optional[float]:
    """Cohen's kappa for two binary raters (e.g. judge vs human)."""
    n = len(a)
    if n == 0 or n != len(b):
        return None
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if abs(1 - pe) < 1e-12:
        return 1.0 if po == 1 else 0.0
    return (po - pe) / (1 - pe)


VERDICTS = ("regression", "improvement", "no_significant_change", "inconclusive")


def compare_binary(k_base: int, n_base: int, k_cand: int, n_cand: int, alpha: float = 0.05,
                   min_effect: float = 0.2, power: float = 0.8) -> Dict[str, Any]:
    """Compare two independent sets of trials of the SAME test."""
    p_b = k_base / n_base if n_base else 0.0
    p_c = k_cand / n_cand if n_cand else 0.0
    lo, hi = newcombe_diff_interval(k_base, n_base, k_cand, n_cand, alpha)
    return {
        "base": {"passed": k_base, "trials": n_base, "rate": p_b},
        "cand": {"passed": k_cand, "trials": n_cand, "rate": p_c},
        "effect": p_c - p_b,
        "ci": [lo, hi],
        "p_value": fisher_exact_two_sided(k_base, n_base, k_cand, n_cand),
        "mde": min_detectable_effect(p_b, n_base, n_cand, alpha, power),
        "min_effect": min_effect,
    }


def decide_verdict(effect: float, q_value: float, mde: float, alpha: float, min_effect: float,
                   ci: Optional[Sequence[float]] = None) -> str:
    """Verdict from an adjusted p-value plus a power check.

    - significant (q < alpha): regression or improvement by sign of effect
    - not significant but the test could have detected `min_effect`: no_significant_change
    - not significant and underpowered: inconclusive
    """
    if q_value < alpha and effect < 0:
        return "regression"
    if q_value < alpha and effect > 0:
        return "improvement"
    if ci is not None and ci[0] > -min_effect and ci[1] < min_effect:
        return "no_significant_change"
    if mde <= min_effect:
        return "no_significant_change"
    return "inconclusive"


# ---------------------------------------------------------------------------
# 5. Hash chain (tamper evidence for run logs)
# ---------------------------------------------------------------------------

GENESIS = "sha256:" + "0" * 64


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def chain_hash(prev: str, record: Dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k not in ("hash", "prev_hash")}
    return "sha256:" + hashlib.sha256((prev + canonical_json(body)).encode("utf-8")).hexdigest()


def verify_chain(records: Iterable[Dict[str, Any]], start: str = GENESIS) -> Tuple[bool, str, int]:
    """Return (ok, head_hash, index_of_first_bad_record_or_-1)."""
    prev = start
    for i, r in enumerate(records):
        if r.get("prev_hash") != prev or chain_hash(prev, r) != r.get("hash"):
            return (False, prev, i)
        prev = r["hash"]
    return (True, prev, -1)


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

REDACT_RE = re.compile(r"(sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
                       r"ghp_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|"
                       r"(?i:bearer)\s+[A-Za-z0-9._-]{20,})")


def redact(text: str) -> str:
    """Mask common credential shapes before anything is written to disk."""
    return REDACT_RE.sub("[REDACTED]", text) if text else text


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)
