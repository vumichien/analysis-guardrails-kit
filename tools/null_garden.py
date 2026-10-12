"""The garden of simple analyses on the synthetic null survey.

576 specifications: 4 outcomes x 3 coffee measures x 16 samples x 3 control sets,
each an OLS fit with HC1 standard errors. sweep(df) fits them all and returns one row
per specification (sample, outcome, exposure, controls, estimate, z, p).
"""
import itertools
import warnings

import pandas as pd
import statsmodels.formula.api as smf

warnings.filterwarnings("ignore")

OUTCOMES = ["systolic_bp", "diastolic_bp", "hypertension", "resting_heart_rate"]
EXPOSURES = ["coffee_cups_day", "caffeine_mg_day", "coffee_drinker"]
CONTROLS = {
    "none": "",
    "demographics": " + age + female + C(education)",
    "full": " + age + female + C(education) + bmi + smoker + alcohol_drinks_week"
            " + salt_score + exercise_hours_week + stress_score + C(region) + C(wave)",
}


def samples(df):
    yield "all", df
    for r in sorted(df.region.unique()):
        yield f"region={r}", df[df.region == r]
    for w in sorted(df.wave.unique()):
        yield f"wave={w}", df[df.wave == w]
    yield "female", df[df.female == 1]
    yield "male", df[df.female == 0]
    yield "age<40", df[df.age < 40]
    yield "age40-59", df[(df.age >= 40) & (df.age < 60)]
    yield "age60+", df[df.age >= 60]
    yield "no_bp_meds", df[df.on_bp_medication == 0]


def sweep(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (sname, sub), o, e, (cname, ctl) in itertools.product(
            list(samples(df)), OUTCOMES, EXPOSURES, CONTROLS.items()):
        d = sub.dropna(subset=[o, e])
        try:
            fit = smf.ols(f"{o} ~ {e}{ctl}", data=d).fit(cov_type="HC1")
        except Exception:
            continue
        b, p = fit.params[e], fit.pvalues[e]
        rows.append({"sample": sname, "outcome": o, "exposure": e, "controls": cname,
                     "estimate": b, "z": b / fit.bse[e], "p": p})
    return pd.DataFrame(rows)
