"""The synthetic "coffee and blood pressure" survey where coffee has no effect.

build(n, seed) returns the survey as a DataFrame. Every coffee column is generated
independently of every other column, so the true coffee effect on every outcome is
exactly zero. make-null-dataset.py is the command-line wrapper.
"""
import numpy as np
import pandas as pd


def build(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    # People and survey design
    wave = rng.choice([2019, 2021, 2023], size=n)
    region = rng.choice(list("ABCDEF"), size=n, p=[0.22, 0.18, 0.17, 0.16, 0.15, 0.12])
    age = np.clip(rng.normal(48, 16, n), 18, 85).round()
    female = rng.binomial(1, 0.52, n)
    education = rng.choice([1, 2, 3, 4], size=n, p=[0.15, 0.35, 0.32, 0.18])
    income_band = np.clip(np.round(education * 0.8 + rng.normal(1.2, 1.0, n)), 1, 5)
    smoker = rng.binomial(1, np.clip(0.28 - 0.04 * education, 0.05, 0.4))
    exercise_hours_week = np.clip(rng.gamma(2.0, 1.6, n) + 0.3 * education - 0.03 * (age - 48), 0, 20).round(1)
    bmi = np.clip(rng.normal(27.5, 5.0, n) - 0.25 * exercise_hours_week + 0.02 * (age - 48), 16, 55).round(1)
    alcohol_drinks_week = np.clip(rng.poisson(3 + 2 * (1 - female)), 0, 40)
    salt_score = np.clip(np.round(rng.normal(3, 1, n)), 1, 5)
    sleep_hours = np.clip(rng.normal(7.0, 1.1, n), 3, 11).round(1)
    stress_score = np.clip(np.round(rng.normal(4.5, 2.2, n) - 0.3 * (income_band - 3)), 0, 10)
    region_shift = pd.Series(region).map({"A": 0.0, "B": 1.5, "C": -1.0, "D": 2.0, "E": 0.5, "F": -0.5}).to_numpy()
    wave_shift = pd.Series(wave).map({2019: 0.0, 2021: 0.8, 2023: 1.2}).to_numpy()

    # Outcomes depend on covariates only (never on coffee)
    latent_sbp = (
        112 + 0.45 * (age - 48) + 0.9 * (bmi - 27.5) - 4.0 * female + 3.0 * smoker
        + 0.35 * alcohol_drinks_week + 1.8 * (salt_score - 3) - 0.5 * exercise_hours_week
        + 0.6 * stress_score + region_shift + wave_shift + rng.normal(0, 11, n)
    )
    risk = 1 / (1 + np.exp(-(latent_sbp - 135) / 6))
    on_bp_medication = rng.binomial(1, np.clip(risk * 0.7, 0, 0.9))
    systolic_bp = np.round(latent_sbp - 9 * on_bp_medication + rng.normal(0, 4, n))
    diastolic_bp = np.round(0.45 * systolic_bp + 18 + 0.3 * (bmi - 27.5) + rng.normal(0, 6, n))
    hypertension = ((systolic_bp >= 130) | (diastolic_bp >= 80) | (on_bp_medication == 1)).astype(int)
    resting_heart_rate = np.round(
        70 - 0.6 * exercise_hours_week + 0.4 * stress_score + 2.5 * smoker + rng.normal(0, 9, n)
    )

    # Coffee block: correlated with itself, independent of everything above
    coffee_cups_day = np.clip(rng.poisson(rng.gamma(1.6, 1.2, n)), 0, 10)
    caffeine_mg_day = np.clip(95 * coffee_cups_day + rng.gamma(1.5, 30, n) + rng.normal(0, 25, n), 0, None).round()
    coffee_drinker = (coffee_cups_day >= 1).astype(int)

    df = pd.DataFrame({
        "id": np.arange(1, n + 1),
        "wave": wave, "region": region, "age": age.astype(int), "female": female,
        "education": education, "income_band": income_band.astype(int), "smoker": smoker,
        "bmi": bmi, "exercise_hours_week": exercise_hours_week,
        "alcohol_drinks_week": alcohol_drinks_week, "salt_score": salt_score.astype(int),
        "sleep_hours": sleep_hours, "stress_score": stress_score.astype(int),
        "on_bp_medication": on_bp_medication,
        "coffee_cups_day": coffee_cups_day.astype(int), "caffeine_mg_day": caffeine_mg_day,
        "coffee_drinker": coffee_drinker,
        "systolic_bp": systolic_bp, "diastolic_bp": diastolic_bp,
        "hypertension": hypertension, "resting_heart_rate": resting_heart_rate,
    })

    # Missing values, completely at random, in a few columns
    for col, share in [("caffeine_mg_day", 0.06), ("income_band", 0.04), ("sleep_hours", 0.03),
                       ("exercise_hours_week", 0.03), ("diastolic_bp", 0.02)]:
        mask = rng.random(n) < share
        df[col] = df[col].astype("float").mask(mask)
    return df
