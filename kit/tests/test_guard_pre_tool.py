"""Replay tool calls through guard-pre-tool.py in both modes and check every T1-T3 rule."""
import json
import subprocess
import sys

import pytest

from conftest import KIT, build_workspace, lock_and_run

HOOK = KIT / "hooks" / "guard-pre-tool.py"
KIT_PY = ".claude/skills/analysis-guardrails/scripts"


def call(ws, mode, tool, tool_input):
    event = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
             "tool_use_id": "t1", "cwd": str(ws)}
    p = subprocess.run([sys.executable, str(HOOK), "--mode", mode, "--workspace", str(ws),
                        "--log", str(ws.parent / "decisions.jsonl")],
                       input=json.dumps(event), capture_output=True, text=True)
    return p


def check(ws, tool, tool_input, expect_rule):
    """expect_rule: None (allowed) or 'T1'/'T2'/'T3'. Checks enforce and log mode."""
    enf = call(ws, "enforce", tool, tool_input)
    assert enf.returncode == 0, enf.stderr
    if expect_rule:
        out = json.loads(enf.stdout)["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny"
        assert out["permissionDecisionReason"]
    else:
        assert enf.stdout.strip() == "", enf.stdout
    log = call(ws, "log", tool, tool_input)
    assert log.returncode == 0
    verdict = json.loads(log.stdout)
    assert "hookSpecificOutput" not in verdict
    rules = [r["rule"] for r in verdict["rules"]]
    if expect_rule:
        assert expect_rule in rules, verdict
    else:
        assert rules == [], verdict
    return verdict


BEFORE_LOCK = [
    ("Read", {"file_path": "data/survey.csv"}, "T1"),
    ("Read", {"file_path": "ABS:data/survey.csv"}, "T1"),
    ("Read", {"file_path": "data/blind.csv"}, None),
    ("Read", {"file_path": "data/codebook.txt"}, None),
    ("Grep", {"pattern": "1", "path": "data/survey.csv"}, "T1"),
    ("Grep", {"pattern": "1", "path": "data", "glob": "*.csv"}, "T1"),
    ("Grep", {"pattern": "1", "path": "data", "glob": "*.txt"}, None),
    ("Grep", {"pattern": "import", "path": ".", "type": "py"}, None),
    ("Grep", {"pattern": "1"}, "T1"),
    ("Bash", {"command": "head -5 data/survey.csv"}, "T1"),
    ("Bash", {"command": "python -c \"import pandas as pd; print(pd.read_csv('data/survey.csv').shape)\""}, "T1"),
    ("Bash", {"command": "ls data/*"}, "T1"),
    ("Bash", {"command": "python explore.py"}, "T1"),
    ("Bash", {"command": "python analysis_locked.py data/blind.csv"}, None),
    ("Bash", {"command": "python3 blind_check.py"}, None),
    ("Glob", {"pattern": "data/*.csv"}, None),
    ("Write", {"file_path": "explore2.py", "content": "pd.read_csv('data/survey.csv')"}, None),
    ("Write", {"file_path": "plan.json", "content": "{}"}, None),
    ("Bash", {"command": f"python {KIT_PY}/make-blind.py"}, None),
    ("Write", {"file_path": "guardrail.json", "content": "{}"}, "T3"),
    ("Edit", {"file_path": ".claude/skills/analysis-guardrails/SKILL.md", "old_string": "a", "new_string": "b"},
     "T3"),
    ("Bash", {"command": "echo {} > guardrail.json"}, "T3"),
    ("Bash", {"command": "rm -r .claude/skills"}, "T3"),
]

AFTER_LOCK = [
    ("Read", {"file_path": "data/survey.csv"}, None),
    ("Bash", {"command": "python explore.py"}, None),
    ("Write", {"file_path": "plan.json", "content": "{}"}, "T2"),
    ("Edit", {"file_path": "analysis_locked.py", "old_string": "a", "new_string": "b"}, "T2"),
    ("Bash", {"command": "cp other.csv results_final.csv"}, "T2"),
    ("Bash", {"command": "python fix_final.py"}, "T2"),
    ("Bash", {"command": "python -c \"open('locked_result.json','w').write('{}')\""}, "T2"),
    ("Bash", {"command": f"python {KIT_PY}/run-locked.py"}, None),
    ("Bash", {"command": f"python {KIT_PY}/lock-plan.py"}, None),
    ("Bash", {"command": "cat results_final.csv"}, None),
    ("Bash", {"command": "python analysis_r3.py > r3.log 2>&1"}, None),
    ("Write", {"file_path": "results_r3.csv", "content": "x"}, None),
    ("Write", {"file_path": "guardrail.json", "content": "{}"}, "T3"),
]


def _prepare(ws, tool_input):
    (ws / "explore.py").write_text("import pandas as pd\ndf = pd.read_csv('data/survey.csv')\n")
    (ws / "blind_check.py").write_text("import pandas as pd\ndf = pd.read_csv('data/blind.csv')\n")
    (ws / "fix_final.py").write_text("import pandas as pd\npd.DataFrame().to_csv('results_final.csv')\n")
    ti = dict(tool_input)
    if str(ti.get("file_path", "")).startswith("ABS:"):
        ti["file_path"] = str(ws / ti["file_path"][4:])
    return ti


@pytest.mark.parametrize("tool,tool_input,rule", BEFORE_LOCK)
def test_before_lock(ws, tool, tool_input, rule):
    v = check(ws, tool, _prepare(ws, tool_input), rule)
    assert v["lock"] == "none"


@pytest.fixture(scope="module")
def locked_ws(tmp_path_factory):
    ws = build_workspace(tmp_path_factory.mktemp("locked"))
    lock_and_run(ws)
    return ws


@pytest.mark.parametrize("tool,tool_input,rule", AFTER_LOCK)
def test_after_lock(locked_ws, tool, tool_input, rule):
    v = check(locked_ws, tool, _prepare(locked_ws, tool_input), rule)
    assert v["lock"] == "valid"


def test_make_blind_is_recorded(ws):
    v = check(ws, "Bash", {"command": f"python {KIT_PY}/make-blind.py"}, None)
    assert v["make_blind"] is True


def test_script_hashes_recorded(ws):
    _prepare(ws, {})
    v = check(ws, "Bash", {"command": "python blind_check.py"}, None)
    assert any(k.endswith("blind_check.py") and len(h) == 64 for k, h in v["scripts"].items())


def test_baseline_workspace_never_denies(tmp_path):
    ws = build_workspace(tmp_path, guardrail=False)
    v = check(ws, "Read", {"file_path": "data/survey.csv"}, None)
    assert v["applies"] is False and v["lock"] is None


def test_broken_lock_counts_as_locked(tmp_path):
    ws = build_workspace(tmp_path)
    lock_and_run(ws)
    (ws / "plan.json").write_text('{"changed": true}')
    v = check(ws, "Read", {"file_path": "data/survey.csv"}, None)
    assert v["lock"] == "broken"


def test_internal_error_fails_with_exit_1(tmp_path):
    ws = build_workspace(tmp_path)
    (ws / "guardrail.json").write_text("{not json")
    p = call(ws, "enforce", "Read", {"file_path": "data/survey.csv"})
    assert p.returncode == 1
    p = call(ws, "log", "Read", {"file_path": "data/survey.csv"})
    assert p.returncode == 0 and "error" in json.loads(p.stdout)
