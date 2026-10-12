"""Stop-gate fixtures: one finished workspace that passes, and one failure for each of G1-G6."""
import csv
import json
import subprocess
import sys

import pytest

from conftest import KIT, build_workspace, lock_and_run, run_script
from guardrail_common import CSV_FIELDS

GATE = KIT / "hooks" / "stop-gate.py"


def gate(ws, mode):
    return subprocess.run([sys.executable, str(GATE), "--mode", mode, "--workspace", str(ws)],
                          input=json.dumps({"hook_event_name": "Stop", "stop_hook_active": False}),
                          capture_output=True, text=True)


def finished(ws):
    lock_and_run(ws)
    with open(ws / "results_r1.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for i, (e, s, p) in enumerate([(0.5, 0.2, 0.01), (-0.1, 0.2, 0.6), (0.05, 0.1, 0.62)]):
            w.writerow({"model_id": i, "estimate": e, "se": s, "pvalue": p, "outcome": "y", "exposure": "x",
                        "model_type": "OLS", "sample": "all", "controls": "none", "n_obs": 100, "notes": ""})
    run_script(ws, "summarize-curve.py")
    est = json.loads((ws / "locked_result.json").read_text())["result"]["estimate"]
    (ws / "report.txt").write_text(f"Headline: estimate {est:.4g}.\n"
                                   f"Curve: 3 specifications explored.\nThe exploration does not change it.\n")
    return ws


@pytest.fixture
def done(tmp_path):
    return finished(build_workspace(tmp_path))


def test_finished_workspace_passes(done):
    p = gate(done, "enforce")
    assert p.returncode == 0, p.stderr
    rec = json.loads(gate(done, "log").stdout)
    assert rec["all_ok"] and [c["id"] for c in rec["checks"]] == ["G1", "G2", "G3", "G4", "G5", "G6"]


def _break_g1(ws):
    (ws / "analysis_locked.py").write_text((ws / "analysis_locked.py").read_text() + "\n# changed\n")


def _break_g2(ws):
    rec = json.loads((ws / "locked_result.json").read_text())
    rec["plan_sha256"] = "0" * 64
    (ws / "locked_result.json").write_text(json.dumps(rec))


def _break_g3(ws):
    with open(ws / "results_final.csv", "a") as f:
        f.write("final,9,1,0.001,systolic_bp,coffee_cups_day,OLS,all,\"age, female\",3000,picked\n")


def _break_g4(ws):
    (ws / "analysis_final.py").write_text("print('my own final')\n")


def _break_g5(ws):
    with open(ws / "results_r1.csv", "a") as f:
        f.write("9,1.0,0.1,0.0001,y,x,OLS,all,none,100,\n")


def _break_g6(ws):
    (ws / "report.txt").write_text("Coffee raises blood pressure.\n")


@pytest.mark.parametrize("gid,breaker", [("G1", _break_g1), ("G2", _break_g2), ("G3", _break_g3),
                                         ("G4", _break_g4), ("G5", _break_g5), ("G6", _break_g6)])
def test_each_check_fails(done, gid, breaker):
    breaker(done)
    p = gate(done, "enforce")
    assert p.returncode == 2
    assert gid in p.stderr
    rec = json.loads(gate(done, "log").stdout)
    assert not next(c for c in rec["checks"] if c["id"] == gid)["ok"]
    assert gate(done, "log").returncode == 0


def test_missing_everything_blocks(ws):
    p = gate(ws, "enforce")
    assert p.returncode == 2 and "G1" in p.stderr and "G6" in p.stderr


def test_baseline_workspace_is_not_gated(tmp_path):
    ws = build_workspace(tmp_path, guardrail=False)
    assert gate(ws, "enforce").returncode == 0
    assert json.loads(gate(ws, "log").stdout)["applies"] is False
