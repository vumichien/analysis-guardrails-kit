"""Shared rules for the analysis guardrails: the lock, the call-level checks, the end-state checks.

One implementation serves three callers: the hook that can refuse a tool call
(guard-pre-tool.py), the end-of-session gate (stop-gate.py), and the kit's own scripts.

What it checks, given a workspace with a guardrail.json:
  T1  the real data file was touched before the analysis plan was locked;
  T2  a locked file was changed after the lock;
  T3  the guardrail's own configuration (.claude/**, guardrail.json) was edited.
  G1-G6  the end state: a valid lock, a locked result, a final row that matches it,
         an untouched final script, a curve summary, and a report that states both.

Threat model. These checks are built for a cooperative agent that drifts, not for an
adversary. They read tool calls (paths, command text, the text of a script a command
runs), not intentions. A file name assembled at run time, or a script that reads the
real path out of guardrail.json, slips past the call-level checks; the end-state checks
and an independent rerun of the final script catch what they miss.

Standard library only, so a hook that imports it cannot fail on a missing package.
"""
import csv
import fnmatch
import hashlib
import json
import math
import os
import re
import shlex
from pathlib import Path

CONFIG_NAME = "guardrail.json"
LOCK_NAME = "plan.lock"
PLAN_NAME = "plan.json"
LOCKED_SCRIPT = "analysis_locked.py"
LOCKED_RESULT = "locked_result.json"
FINAL_CSV = "results_final.csv"
FINAL_SCRIPT = "analysis_final.py"
CURVE_SUMMARY = "curve_summary.json"
REPORT = "report.txt"
DEFAULT_PROTECTED = [PLAN_NAME, LOCKED_SCRIPT, LOCK_NAME, LOCKED_RESULT, FINAL_CSV, FINAL_SCRIPT]
PLAN_KEYS = ["outcome", "exposure", "sample", "controls", "model_family", "standard_errors",
             "expected_direction", "alpha"]
CSV_FIELDS = ["model_id", "estimate", "se", "pvalue", "outcome", "exposure", "model_type",
              "sample", "controls", "n_obs", "notes"]
KIT_SCRIPTS = {"make-blind.py", "lock-plan.py", "run-locked.py", "summarize-curve.py"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Shell commands that change a file named among their arguments
SHELL_WRITERS = {"cp", "mv", "rm", "tee", "touch", "truncate", "ln", "install", "rsync", "dd"}
# Python (or inline code) that writes: checked on the same line as a protected name
PY_WRITE = re.compile(r"""open\([^)]*['"][wax]b?\+?['"]|\.to_csv\(|\.to_json\(|write_text\(|write_bytes\("""
                      r"""|shutil\.|os\.remove\(|os\.unlink\(|os\.rename\(|os\.replace\(|\.unlink\(|\.rename\(""")
REDIRECT = re.compile(r"(?<![0-9&<>=!])>>?\s*([^\s;|&]+)")


# ── basics ────────────────────────────────────────────────────────────────

def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_config(workspace):
    """The workspace's guardrail.json as a dict, or None (no guardrail in this workspace)."""
    p = Path(workspace) / CONFIG_NAME
    if not p.exists():
        return None
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg.setdefault("protected_files", list(DEFAULT_PROTECTED))
    return cfg


def real_path(workspace, cfg):
    return (Path(workspace) / cfg["real_data"]).resolve()


def blind_path(workspace, cfg):
    return (Path(workspace) / cfg["blind_data"]).resolve()


def resolve(path, cwd):
    p = Path(str(path)).expanduser()
    if not p.is_absolute():
        p = Path(cwd) / p
    return Path(_normpath(str(p))).resolve()


def _normpath(p):
    return os.path.normpath(p)


def controls_str(value):
    """One canonical string for a plan's controls (a list or a string)."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v).strip() for v in value)
    return str(value).strip()


# ── the lock ──────────────────────────────────────────────────────────────

def lock_state(workspace):
    """'none' (no plan.lock), 'valid' (hashes match), or 'broken' (a lock whose files changed).

    A broken lock still counts as locked: the plan was fixed, then changed."""
    ws = Path(workspace)
    lock = ws / LOCK_NAME
    if not lock.exists():
        return "none"
    try:
        rec = json.loads(lock.read_text(encoding="utf-8"))
        ok = (rec.get("plan_sha256") == sha256_file(ws / PLAN_NAME)
              and rec.get("script_sha256") == sha256_file(ws / LOCKED_SCRIPT))
    except (OSError, ValueError):
        ok = False
    return "valid" if ok else "broken"


def is_locked(workspace):
    return lock_state(workspace) != "none"


# ── reading a tool call ───────────────────────────────────────────────────

def _py_files_run(command, cwd):
    """Paths of .py files a shell command runs (python x.py, python3 -u x.py, ./x.py)."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    out = []
    for t in tokens:
        t = t.strip("'\"();")
        if t.endswith(".py"):
            out.append(resolve(t, cwd))
    return out


def _is_kit_script(path, workspace):
    p = Path(path)
    kit_dir = (Path(workspace) / ".claude" / "skills" / "analysis-guardrails" / "scripts").resolve()
    return p.name in KIT_SCRIPTS and p.resolve().parent == kit_dir


def _names_real(text, cfg):
    """Does this text name the real data file, or a wildcard over its folder?"""
    name = Path(cfg["real_data"]).name
    folder = Path(cfg["real_data"]).parent.as_posix()
    if name in text:
        return True
    if folder and folder != ".":
        if re.search(re.escape(folder) + r"/[^\s'\"]*[*?\[]", text):
            return True
    return False


def _shell_write_targets(command):
    """Paths a shell command writes to, moves, or deletes (redirects, cp/mv/rm/tee/..., sed -i)."""
    targets = [t.strip("'\"") for t in REDIRECT.findall(command)]
    for segment in re.split(r"[;&|]+", command):
        try:
            toks = shlex.split(segment, posix=True)
        except ValueError:
            toks = segment.split()
        if not toks:
            continue
        cmd = Path(toks[0]).name
        args = [t for t in toks[1:] if not t.startswith("-")]
        if cmd in SHELL_WRITERS and args:
            # cp/install/rsync/ln write their last argument; the others touch every argument
            targets += args[-1:] if cmd in {"cp", "install", "rsync", "ln"} else args
        if cmd == "sed" and "-i" in toks:
            targets += args[1:]
    return targets


def _shell_writes(command, names):
    """Names (from `names`) that a shell command, or inline code in it, writes to."""
    hits = {Path(t).name for t in _shell_write_targets(command) if Path(t).name in names}
    return hits | _py_text_writes(command, names)


def _py_text_writes(text, names):
    hits = set()
    for line in text.splitlines():
        if PY_WRITE.search(line):
            for n in names:
                if n in line:
                    hits.add(n)
    return hits


def classify(event, workspace):
    """Read one PreToolUse event and say which rules it breaks.

    Returns a dict: applies (False when the workspace has no guardrail.json), lock
    (none/valid/broken), rules (list of T1/T2/T3 hits with details), deny (bool),
    reason (str), scripts (sha256 of every .py file the call runs), make_blind (bool)."""
    ws = Path(workspace).resolve()
    tool = event.get("tool_name", "")
    tin = event.get("tool_input") or {}
    cwd = event.get("cwd") or str(ws)
    out = {"applies": False, "lock": None, "rules": [], "deny": False, "reason": "",
           "scripts": {}, "make_blind": False, "tool": tool}

    if tool == "Bash":
        for py in _py_files_run(tin.get("command", ""), cwd):
            try:
                out["scripts"][str(py)] = sha256_file(py)
            except OSError:
                out["scripts"][str(py)] = None

    cfg = load_config(ws)
    if cfg is None:
        return out
    out["applies"] = True
    state = lock_state(ws)
    out["lock"] = state
    locked = state != "none"
    real = real_path(ws, cfg)
    protected = set(cfg["protected_files"])
    rules = out["rules"]

    # T3: the guardrail's own configuration, at any time
    if tool in WRITE_TOOLS:
        target = resolve(tin.get("file_path") or tin.get("notebook_path") or "", cwd)
        rel = _rel(target, ws)
        if rel is not None and (rel == CONFIG_NAME or rel.startswith(".claude/")):
            rules.append({"rule": "T3", "detail": f"{tool} on {rel}"})
    if tool == "Bash":
        cmd = tin.get("command", "")
        targets = _shell_write_targets(cmd)
        if (any(Path(t).name == CONFIG_NAME or ".claude/" in t for t in targets)
                or _py_text_writes(cmd, {CONFIG_NAME, ".claude/"})):
            rules.append({"rule": "T3", "detail": "Bash writes guardrail.json or under .claude/"})

    # T1: touching the real data before the lock
    if not locked:
        if tool == "Read" and resolve(tin.get("file_path", ""), cwd) == real:
            rules.append({"rule": "T1", "detail": "Read of the real data file"})
        elif tool == "Grep":
            target = resolve(tin.get("path") or cwd, cwd)
            glob = tin.get("glob")
            ftype = tin.get("type")
            if target == real:
                rules.append({"rule": "T1", "detail": "Grep on the real data file"})
            elif _is_ancestor(target, real) and not ftype and (
                    not glob or fnmatch.fnmatch(real.name, glob) or fnmatch.fnmatch(_rel(real, ws) or "", glob)):
                rules.append({"rule": "T1", "detail": "Grep over the real data file's folder"})
        elif tool == "Bash":
            cmd = tin.get("command", "")
            runs = _py_files_run(cmd, cwd)
            if any(_is_kit_script(p, ws) and p.name == "make-blind.py" for p in runs):
                out["make_blind"] = True
            elif _names_real(cmd, cfg):
                rules.append({"rule": "T1", "detail": "Bash command names the real data file"})
            else:
                for p in runs:
                    if _is_kit_script(p, ws):
                        continue
                    try:
                        text = p.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    if _names_real(text, cfg):
                        rules.append({"rule": "T1", "detail": f"runs {p.name}, which names the real data file"})
                        break

    # T2: changing a locked file after the lock
    if locked:
        if tool in WRITE_TOOLS:
            target = resolve(tin.get("file_path") or tin.get("notebook_path") or "", cwd)
            rel = _rel(target, ws)
            if rel in protected:
                rules.append({"rule": "T2", "detail": f"{tool} on locked file {rel}"})
        elif tool == "Bash":
            cmd = tin.get("command", "")
            runs = _py_files_run(cmd, cwd)
            if not any(_is_kit_script(p, ws) for p in runs):
                hits = _shell_writes(cmd, protected)
                if hits:
                    rules.append({"rule": "T2", "detail": f"Bash writes locked file(s) {sorted(hits)}"})
                for p in runs:
                    try:
                        text = p.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    hits = _py_text_writes(text, protected)
                    if hits:
                        rules.append({"rule": "T2", "detail": f"runs {p.name}, which writes {sorted(hits)}"})
                        break

    if rules:
        out["deny"] = True
        out["reason"] = deny_reason(rules[0]["rule"])
    return out


def deny_reason(rule):
    return {
        "T1": "The plan is not locked yet, so the real data file stays closed. Work on data/blind.csv; "
              "see the analysis-guardrails skill, steps 2-3 (write plan.json and analysis_locked.py, "
              "test them on the blind copy, then lock).",
        "T2": "This file is locked. The plan, the locked script and the final result can't change after "
              "the lock; explore in new files and report the exploration as a curve "
              "(analysis-guardrails skill, steps 5-7). Rerunning run-locked.py is allowed.",
        "T3": "The guardrail's own configuration (.claude/ and guardrail.json) can't be edited.",
    }[rule]


def _rel(path, ws):
    try:
        return Path(path).resolve().relative_to(Path(ws).resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _is_ancestor(folder, path):
    try:
        Path(path).relative_to(Path(folder))
        return True
    except ValueError:
        return False


# ── round files and the curve ─────────────────────────────────────────────

def round_files(workspace):
    ws = Path(workspace)
    files = [p for p in ws.glob("results_r*.csv") if re.fullmatch(r"results_r\d+\.csv", p.name)]
    return sorted(files, key=lambda p: int(re.search(r"\d+", p.name).group()))


def read_rows(path):
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def count_round_rows(workspace):
    return sum(len(read_rows(p)) for p in round_files(workspace))


def to_float(x):
    try:
        v = float(str(x).strip())
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# ── the end state (Stop gate) ─────────────────────────────────────────────

NUMBER = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def prints_number(text, target):
    """Does `text` print `target`: some decimal number (with a point or an exponent, and at least two
    significant digits) that equals it to the precision printed, or to 1e-6 relative when more digits
    are printed? Counts such as "Round 1" or "36 specifications" never match."""
    if target is None:
        return False
    text = text.replace("−", "-").replace("×10^", "e").replace(" x 10^", "e")
    for tok in NUMBER.findall(text):
        try:
            v = float(tok)
        except ValueError:
            continue
        mant = tok.lower().split("e")[0]
        if ("." not in mant and "e" not in tok.lower()) or len(mant.lstrip("+-").replace(".", "").lstrip("0")) < 2:
            continue  # a count, or too few digits to identify the number
        decimals = len(mant.split(".")[1]) if "." in mant else 0
        exp = int(tok.lower().split("e")[1]) if "e" in tok.lower() else 0
        if abs(v - target) <= max(0.5 * 10 ** (exp - decimals), 1e-6 * abs(target)):
            return True
    return False


def _close(a, b, rel=1e-9):
    if a is None or b is None:
        return False
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-15)


def stop_checks(workspace):
    """G1-G6 on the files only (never runs any code). Returns a list of dicts: id, ok, why."""
    ws = Path(workspace)
    res = []

    def add(gid, ok, why):
        res.append({"id": gid, "ok": bool(ok), "why": "" if ok else why})

    state = lock_state(ws)
    add("G1", state == "valid",
        "plan.lock is missing; lock the plan with scripts/lock-plan.py (skill step 3)" if state == "none" else
        "plan.json or analysis_locked.py changed after the lock")

    locked = None
    try:
        locked = json.loads((ws / LOCKED_RESULT).read_text(encoding="utf-8"))
        lock = json.loads((ws / LOCK_NAME).read_text(encoding="utf-8"))
        ok = (locked.get("plan_sha256") == lock.get("plan_sha256")
              and locked.get("script_sha256") == lock.get("script_sha256"))
        add("G2", ok, "locked_result.json does not record the lock's hashes; rerun scripts/run-locked.py")
    except (OSError, ValueError):
        locked = None
        add("G2", False, "locked_result.json is missing; run scripts/run-locked.py (skill step 4)")

    try:
        rows = read_rows(ws / FINAL_CSV)
        plan = json.loads((ws / PLAN_NAME).read_text(encoding="utf-8"))
        r = rows[0] if len(rows) == 1 else None
        ok = (r is not None and r.get("model_id") == "final" and locked is not None
              and _close(to_float(r.get("estimate")), to_float(locked["result"].get("estimate")))
              and _close(to_float(r.get("se")), to_float(locked["result"].get("se")))
              and _close(to_float(r.get("pvalue")), to_float(locked["result"].get("p")))
              and r.get("outcome") == str(plan.get("outcome"))
              and r.get("exposure") == str(plan.get("exposure"))
              and r.get("sample") == str(plan.get("sample"))
              and r.get("controls") == controls_str(plan.get("controls", "")))
        add("G3", ok, "results_final.csv must hold exactly one row, model_id=final, equal to the locked "
                      "result; rerun scripts/run-locked.py to restore it")
    except (OSError, ValueError, KeyError, TypeError):
        add("G3", False, "results_final.csv or plan.json is missing; run scripts/run-locked.py")

    try:
        ok = locked is not None and sha256_file(ws / FINAL_SCRIPT) == locked.get("analysis_final_sha256")
        add("G4", ok, "analysis_final.py differs from the one run-locked.py wrote; rerun scripts/run-locked.py")
    except OSError:
        add("G4", False, "analysis_final.py is missing; run scripts/run-locked.py")

    curve = None
    try:
        curve = json.loads((ws / CURVE_SUMMARY).read_text(encoding="utf-8"))
        n = count_round_rows(ws)
        add("G5", curve.get("n_specs") == n and n > 0,
            f"curve_summary.json counts {curve.get('n_specs')} specifications but the round files hold {n}; "
            "rerun scripts/summarize-curve.py (skill step 6)")
    except (OSError, ValueError):
        add("G5", False, "curve_summary.json is missing; run scripts/summarize-curve.py (skill step 6)")

    try:
        text = (ws / REPORT).read_text(encoding="utf-8", errors="replace")
        est = to_float(locked["result"].get("estimate")) if locked else None
        has_head = prints_number(text, est)
        has_count = curve is not None and str(curve.get("n_specs")) in text
        add("G6", has_head and has_count,
            "report.txt must state the locked headline estimate (at least 3 significant figures) and the number of "
            "specifications in the curve (skill step 7)")
    except OSError:
        add("G6", False, "report.txt is missing; write it (skill step 7)")
    return res
