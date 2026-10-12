"""The kit's scripts: lock -> run-locked -> reproduce, make-blind, summarize-curve, and the m-value."""
import csv
import json
import subprocess
import sys

import pandas as pd
import pytest

from conftest import EXPOSURES, KIT, PILOT, LOCKED_SRC, PLAN, lock_and_run, run_script
from guardrail_common import CSV_FIELDS
from m_value import m_value


def test_lock_run_reproduce_round_trip(ws):
    lock_and_run(ws)
    row = list(csv.DictReader(open(ws / "results_final.csv")))
    assert len(row) == 1 and row[0]["model_id"] == "final"
    assert row[0]["controls"] == "age, female" and row[0]["model_type"] == "OLS"
    rerun = subprocess.run([sys.executable, str(ws / "analysis_final.py")], capture_output=True, text=True, cwd="/")
    assert rerun.returncode == 0, rerun.stderr
    assert float(json.loads(rerun.stdout)["estimate"]) == float(row[0]["estimate"])
    first = (ws / "locked_result.json").read_text()
    run_script(ws, "run-locked.py")
    assert json.loads(first)["result"] == json.loads((ws / "locked_result.json").read_text())["result"]


def test_lock_is_final(ws):
    lock_and_run(ws)
    p = run_script(ws, "lock-plan.py", check=False)
    assert p.returncode != 0 and "already exists" in (p.stdout + p.stderr)


def test_lock_refuses_a_data_path_in_the_script(ws):
    (ws / "plan.json").write_text(json.dumps(PLAN))
    (ws / "analysis_locked.py").write_text(LOCKED_SRC.replace("sys.argv[1]", "'data/survey.csv'"))
    p = run_script(ws, "lock-plan.py", check=False)
    assert p.returncode != 0 and not (ws / "plan.lock").exists()


def test_lock_refuses_an_incomplete_plan(ws):
    (ws / "plan.json").write_text(json.dumps({k: v for k, v in PLAN.items() if k != "alpha"}))
    (ws / "analysis_locked.py").write_text(LOCKED_SRC)
    p = run_script(ws, "lock-plan.py", check=False)
    assert p.returncode != 0 and "alpha" in (p.stdout + p.stderr)


def test_run_locked_refuses_without_a_lock(ws):
    p = run_script(ws, "run-locked.py", check=False)
    assert p.returncode != 0 and not (ws / "results_final.csv").exists()


def test_make_blind(ws):
    real, blind = pd.read_csv(PILOT), pd.read_csv(ws / "data" / "blind.csv")
    assert list(real.columns) == list(blind.columns) and len(real) == len(blind)
    others = [c for c in real.columns if c not in EXPOSURES]
    pd.testing.assert_frame_equal(real[others], blind[others])
    for c in EXPOSURES:
        assert sorted(real[c].dropna()) == sorted(blind[c].dropna())
        assert real[c].isna().sum() == blind[c].isna().sum()
    assert not real[EXPOSURES].equals(blind[EXPOSURES])
    rc, bc = real[EXPOSURES].corr(), blind[EXPOSURES].corr()
    pd.testing.assert_frame_equal(rc, bc, atol=1e-12)
    for o in ["systolic_bp", "diastolic_bp", "resting_heart_rate"]:
        assert abs(blind[["coffee_cups_day", o]].corr().iloc[0, 1]) < 0.06


def test_summarize_curve_counts(tmp_path):
    ws = tmp_path
    rows = [(0.5, 0.2, 0.01), (-0.6, 0.2, 0.003), (0.1, 0.2, 0.6), (0.2, 0.0, 0.5), ("nan", 0.1, "")]
    for name, chunk in [("results_r1.csv", rows[:3]), ("results_r2.csv", rows[3:])]:
        with open(ws / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            for i, (e, s, p) in enumerate(chunk):
                w.writerow({"model_id": i, "estimate": e, "se": s, "pvalue": p})
    (ws / "results_final.csv").write_text("model_id,estimate\nfinal,1\n")
    p = subprocess.run([sys.executable, str(KIT / "skills/analysis-guardrails/scripts/summarize-curve.py"),
                        "--workspace", str(ws)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    s = json.loads((ws / "curve_summary.json").read_text())
    assert s["n_specs"] == 5 and s["n_with_estimate_and_se"] == 3
    assert s["share_significant_positive"] == pytest.approx(1 / 5)
    assert s["share_significant_negative"] == pytest.approx(1 / 5)
    assert s["median_z"] == pytest.approx(0.5) and s["z_min"] == pytest.approx(-3.0)
    assert s["files"] == ["results_r1.csv", "results_r2.csv"]


def test_m_value_hand_example():
    ref = [-2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    # z0 = 6: 9 of 10 are <= 6, 2 of 10 are >= 6 -> m = 2 * 0.2 = 0.4
    assert m_value(6.0, ref) == pytest.approx(0.4)
    # z0 = 10: none are >= 10 -> m = 0
    assert m_value(10.0, ref) == 0.0
    # the median of the set: capped at 1
    assert m_value(2.5, ref) == 1.0
    # non-finite reference values are dropped
    assert m_value(6.0, ref + [float("nan"), None]) == pytest.approx(0.4)
    assert m_value(1.0, []) is None


def test_skill_variant_has_the_same_body():
    base = (KIT / "skills/analysis-guardrails/SKILL.md").read_text(encoding="utf-8").split("---", 2)[2]
    var = (KIT / "skill-frontmatter-variant/SKILL.md").read_text(encoding="utf-8").split("---", 2)[2]
    assert base == var
    assert "hooks" not in (KIT / "skills/analysis-guardrails/SKILL.md").read_text(encoding="utf-8").split("---")[1]


def test_prints_number_accepts_any_honest_rendering():
    from guardrail_common import prints_number
    x = -0.0126789
    for text in ["estimate -0.0127 (SE 0.01)", "estimate -0.01268", "-1.27e-2", "\u22120.0127",
                 "-1.27 x 10^-2", '{"estimate": -0.0126789}']:
        assert prints_number(text, x), text
    for text in ["Round 1 of 10", "estimate -0.0130", "estimate 0.0127", "estimate -0.02", "no number here"]:
        assert not prints_number(text, x), text
    assert not prints_number("anything 1.23", None)
    # 3 significant figures with the trailing zero dropped, as Python's .3g prints it
    assert prints_number("Headline -0.015. Curve of 36 specifications.", -0.014990391804531655)
    assert not prints_number("36 specifications", 36.2)
