import atexit
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
REPO = KIT.parent
SCRIPTS_REL = Path(".claude/skills/analysis-guardrails/scripts")
EXPOSURES = ["coffee_cups_day", "caffeine_mg_day", "coffee_drinker"]

sys.path.insert(0, str(KIT / "skills" / "analysis-guardrails" / "scripts"))
sys.path.insert(0, str(REPO / "tools"))

from null_survey import build  # noqa: E402

# The pilot null file (seed 20261010), rebuilt once per test session; the repo keeps no data files.
_PILOT_DIR = Path(tempfile.mkdtemp(prefix="agk-pilot-"))
atexit.register(shutil.rmtree, _PILOT_DIR, ignore_errors=True)
PILOT = _PILOT_DIR / "coffee-bp-null.csv"
build(3000, 20261010).to_csv(PILOT, index=False)

LOCKED_SRC = '''import json
import sys

import pandas as pd
import statsmodels.formula.api as smf

df = pd.read_csv(sys.argv[1]).dropna(subset=["systolic_bp", "coffee_cups_day"])
fit = smf.ols("systolic_bp ~ coffee_cups_day + age + female", data=df).fit(cov_type="HC1")
b, se = float(fit.params["coffee_cups_day"]), float(fit.bse["coffee_cups_day"])
print(json.dumps({"estimate": b, "se": se, "z": b / se, "p": float(fit.pvalues["coffee_cups_day"]),
                  "n_obs": int(fit.nobs)}))
'''

PLAN = {"outcome": "systolic_bp", "exposure": "coffee_cups_day", "sample": "all", "controls": ["age", "female"],
        "model_family": "OLS", "standard_errors": "HC1", "expected_direction": "positive", "alpha": 0.05}


def run_script(ws, name, *args, check=True):
    p = subprocess.run([sys.executable, str(ws / SCRIPTS_REL / name), "--workspace", str(ws), *args],
                       capture_output=True, text=True, cwd=ws)
    if check and p.returncode != 0:
        raise AssertionError(f"{name} failed: {p.stdout}\n{p.stderr}")
    return p


def build_workspace(root, data_src=PILOT, guardrail=True):
    ws = Path(root) / "workspace"
    (ws / "data").mkdir(parents=True)
    shutil.copy(data_src, ws / "data" / "survey.csv")
    (ws / "data" / "codebook.txt").write_text("codebook\n")
    if guardrail:
        shutil.copytree(KIT / "skills" / "analysis-guardrails", ws / ".claude" / "skills" / "analysis-guardrails")
        (ws / "guardrail.json").write_text(json.dumps({
            "real_data": "data/survey.csv", "blind_data": "data/blind.csv",
            "exposure_columns": EXPOSURES, "blind_seed": 2001}))
        run_script(ws, "make-blind.py")
    return ws


def lock_and_run(ws):
    (ws / "plan.json").write_text(json.dumps(PLAN))
    (ws / "analysis_locked.py").write_text(LOCKED_SRC)
    run_script(ws, "lock-plan.py")
    run_script(ws, "run-locked.py")


@pytest.fixture
def ws(tmp_path):
    return build_workspace(tmp_path)
