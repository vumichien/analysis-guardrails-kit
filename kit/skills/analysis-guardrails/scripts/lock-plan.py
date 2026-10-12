"""Lock the analysis plan: record the sha256 of plan.json and analysis_locked.py in plan.lock.

Checks first that plan.json names exactly one primary specification with every field
filled, and that analysis_locked.py takes the data path as its only argument (no data
path written in it). A lock is final: this script refuses to overwrite an existing one.

Usage: python .claude/skills/analysis-guardrails/scripts/lock-plan.py [--workspace .]
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from guardrail_common import (LOCK_NAME, LOCKED_SCRIPT, PLAN_KEYS, PLAN_NAME, load_config,  # noqa: E402
                              sha256_file)


def problems(ws, cfg):
    out = []
    plan_p, script_p = ws / PLAN_NAME, ws / LOCKED_SCRIPT
    if not plan_p.exists():
        return [f"{PLAN_NAME} is missing"]
    if not script_p.exists():
        return [f"{LOCKED_SCRIPT} is missing"]
    try:
        plan = json.loads(plan_p.read_text(encoding="utf-8"))
    except ValueError as e:
        return [f"{PLAN_NAME} is not valid JSON: {e}"]
    if not isinstance(plan, dict):
        return [f"{PLAN_NAME} must be one JSON object: exactly one primary specification"]
    missing = [k for k in PLAN_KEYS if plan.get(k) in (None, "", [])]
    if missing:
        out.append(f"{PLAN_NAME} is missing: {', '.join(missing)}")
    for k in ("outcome", "exposure", "sample", "model_family"):
        if isinstance(plan.get(k), list):
            out.append(f"{PLAN_NAME}: '{k}' must be a single choice, not a list")
    text = script_p.read_text(encoding="utf-8", errors="replace")
    for p in (cfg["real_data"], cfg["blind_data"]):
        if Path(p).name in text:
            out.append(f"{LOCKED_SCRIPT} must not contain a data path ({Path(p).name}); "
                       "take the path as its only argument (sys.argv[1])")
    if "sys.argv" not in text and "argparse" not in text:
        out.append(f"{LOCKED_SCRIPT} must read the data path from its command-line argument")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=".")
    args = ap.parse_args()
    ws = Path(args.workspace).resolve()
    cfg = load_config(ws)
    if cfg is None:
        raise SystemExit("No guardrail.json in this workspace.")
    if (ws / LOCK_NAME).exists():
        raise SystemExit(f"{LOCK_NAME} already exists. A lock is final; it cannot be redone.")
    errs = problems(ws, cfg)
    if errs:
        raise SystemExit("Not locked:\n- " + "\n- ".join(errs))
    rec = {"plan_sha256": sha256_file(ws / PLAN_NAME), "script_sha256": sha256_file(ws / LOCKED_SCRIPT),
           "locked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (ws / LOCK_NAME).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    print(json.dumps({"locked": True, **rec}))


if __name__ == "__main__":
    main()
