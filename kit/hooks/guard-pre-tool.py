"""PreToolUse hook: refuse a tool call that breaks the analysis guardrails (T1-T3).

Reads the hook event (JSON) on stdin.
  --mode enforce  a hit prints a deny decision with the rule and the way forward (exit 0);
                  anything else prints nothing (exit 0). An internal error exits 1, which a
                  hook configured with onFailure: "block" turns into a block.
  --mode log      never refuses: prints the verdict as JSON for a logger and exits 0.
--log FILE appends one JSON line per call (the decision, the lock state, script hashes).

Usage: guard-pre-tool.py --mode enforce|log --workspace <abs path> [--log <file>]
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parent / "skills" / "analysis-guardrails" / "scripts"))


def append(log, rec):
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["enforce", "log"], required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--log")
    args = ap.parse_args()
    try:
        from guardrail_common import classify
        event = json.loads(sys.stdin.read() or "{}")
        verdict = classify(event, args.workspace)
    except Exception as e:  # noqa: BLE001
        append(args.log, {"ts": datetime.now(timezone.utc).isoformat(), "hook": "guard-pre-tool",
                          "mode": args.mode, "error": repr(e)})
        if args.mode == "log":
            print(json.dumps({"error": repr(e)}))
            return 0
        print(f"guard-pre-tool failed: {e!r}", file=sys.stderr)
        return 1

    decision = "deny" if verdict["deny"] and args.mode == "enforce" else "allow"
    append(args.log, {"ts": datetime.now(timezone.utc).isoformat(), "hook": "guard-pre-tool",
                      "mode": args.mode, "tool_use_id": event.get("tool_use_id"), "tool": verdict["tool"],
                      "decision": decision, **{k: verdict[k] for k in ("applies", "lock", "rules", "make_blind",
                                                                         "scripts")}})
    if args.mode == "log":
        print(json.dumps(verdict))
    elif decision == "deny":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": verdict["reason"]}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
