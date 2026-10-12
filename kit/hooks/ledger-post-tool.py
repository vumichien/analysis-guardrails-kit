"""PostToolUse hook: append every tool call to a ledger the agent cannot see.

One JSON line per call: time, tool, a short input summary, and the first 500 characters
of the output. Always exits 0 (a PostToolUse hook cannot block a call that already ran).

Usage: ledger-post-tool.py --log <file>
"""
import argparse
import json
import sys
from datetime import datetime, timezone


def summary(tool_input):
    if not isinstance(tool_input, dict):
        return str(tool_input)[:300]
    for k in ("command", "file_path", "notebook_path", "pattern", "path", "skill"):
        if k in tool_input:
            return f"{k}: {str(tool_input[k])[:300]}"
    return json.dumps(tool_input)[:300]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    args = ap.parse_args()
    try:
        event = json.loads(sys.stdin.read() or "{}")
        resp = event.get("tool_response")
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "hook": "ledger-post-tool",
               "tool_use_id": event.get("tool_use_id"), "tool": event.get("tool_name"),
               "input": summary(event.get("tool_input")),
               "output_head": (resp if isinstance(resp, str) else json.dumps(resp))[:500]}
        with open(args.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:  # noqa: BLE001
        print(f"ledger-post-tool: {e!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
