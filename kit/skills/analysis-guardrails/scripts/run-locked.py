"""Run the locked analysis once on the real data and write the headline.

Verifies plan.lock against plan.json and analysis_locked.py, runs
`python analysis_locked.py <real data>`, and writes:
  locked_result.json   the result, both lock hashes and the hash of analysis_final.py;
  results_final.csv    one row, model_id="final", in the round files' schema;
  analysis_final.py    a short script that reruns the locked analysis on the real data.
Rerunning it regenerates the same files.

The locked script must print one JSON line with estimate, se, z, p and n_obs.

Usage: python .claude/skills/analysis-guardrails/scripts/run-locked.py [--workspace .] [--timeout 900]
"""
import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from guardrail_common import (CSV_FIELDS, FINAL_CSV, FINAL_SCRIPT, LOCK_NAME, LOCKED_RESULT,  # noqa: E402
                              LOCKED_SCRIPT, PLAN_NAME, controls_str, load_config, lock_state, sha256_file,
                              to_float)

FINAL_TEMPLATE = '''import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
sys.exit(subprocess.call([sys.executable, str(here / "{script}"), str(here / "{data}")]))
'''


def parse_result(stdout):
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if all(k in rec for k in ("estimate", "se", "p", "n_obs")):
                return rec
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()
    ws = Path(args.workspace).resolve()
    cfg = load_config(ws)
    if cfg is None:
        raise SystemExit("No guardrail.json in this workspace.")
    state = lock_state(ws)
    if state == "none":
        raise SystemExit("No plan.lock: lock the plan first (scripts/lock-plan.py).")
    if state == "broken":
        raise SystemExit("plan.json or analysis_locked.py changed after the lock; the locked analysis can't run.")

    proc = subprocess.run([sys.executable, str(ws / LOCKED_SCRIPT), str(ws / cfg["real_data"])],
                          capture_output=True, text=True, cwd=ws, timeout=args.timeout)
    result = parse_result(proc.stdout)
    if proc.returncode != 0 or result is None:
        raise SystemExit("The locked script did not print a JSON line with estimate, se, p and n_obs "
                         f"(exit {proc.returncode}).\n{proc.stderr[-2000:]}")
    est, se = to_float(result["estimate"]), to_float(result["se"])
    if "z" not in result and est is not None and se:
        result["z"] = est / se

    plan = json.loads((ws / PLAN_NAME).read_text(encoding="utf-8"))
    lock = json.loads((ws / LOCK_NAME).read_text(encoding="utf-8"))
    (ws / FINAL_SCRIPT).write_text(FINAL_TEMPLATE.format(script=LOCKED_SCRIPT, data=cfg["real_data"]),
                                   encoding="utf-8")
    with open(ws / FINAL_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerow({"model_id": "final", "estimate": result["estimate"], "se": result["se"],
                    "pvalue": result["p"], "outcome": plan["outcome"], "exposure": plan["exposure"],
                    "model_type": plan["model_family"], "sample": plan["sample"],
                    "controls": controls_str(plan["controls"]), "n_obs": result["n_obs"],
                    "notes": f"locked plan; plan sha256 {lock['plan_sha256'][:12]}"})
    rec = {"result": result, "plan_sha256": lock["plan_sha256"], "script_sha256": lock["script_sha256"],
           "analysis_final_sha256": sha256_file(ws / FINAL_SCRIPT),
           "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (ws / LOCKED_RESULT).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    print(json.dumps({"headline": result, "files": [LOCKED_RESULT, FINAL_CSV, FINAL_SCRIPT]}))


if __name__ == "__main__":
    main()
