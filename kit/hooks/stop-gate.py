"""Stop hook: the agent may finish only when the locked headline and the curve are in place (G1-G6).

Checks files only and never runs the analysis, so it cannot time out on a slow model.
Reads the hook event (JSON) on stdin.
  --mode enforce  a failed check prints each failure and how to fix it on stderr and exits 2,
                  which sends the agent back to work (Claude Code stops after 8 in a row).
  --mode log      prints the result as JSON and exits 0.
--log FILE appends one JSON line per stop attempt.

Usage: stop-gate.py --mode enforce|log --workspace <abs path> [--log <file>]
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parent / "skills" / "analysis-guardrails" / "scripts"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["enforce", "log"], required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--log")
    args = ap.parse_args()
    from guardrail_common import load_config, stop_checks
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        event = {}
    applies = load_config(args.workspace) is not None
    checks = stop_checks(args.workspace) if applies else []
    ok = all(c["ok"] for c in checks)
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "hook": "stop-gate", "mode": args.mode,
           "applies": applies, "all_ok": ok, "stop_hook_active": event.get("stop_hook_active"),
           "checks": checks, "decision": "block" if applies and not ok and args.mode == "enforce" else "allow"}
    if args.log:
        with open(args.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    if args.mode == "log":
        print(json.dumps(rec))
        return 0
    if rec["decision"] == "block":
        lines = [f"{c['id']}: {c['why']}" for c in checks if not c["ok"]]
        print("The analysis is not finished. Fix these before stopping:\n" + "\n".join(lines), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
