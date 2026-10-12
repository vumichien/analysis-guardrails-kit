"""Build a synthetic "coffee and blood pressure" survey where coffee has no effect.

Every coffee column is generated independently of every other column, so the true
association between coffee and blood pressure is exactly zero by construction. Any
"significant" coffee effect an analyst reports on this file is a false positive.

The file is built to have the forks real survey data has: three ways to measure
coffee, four outcomes, a dozen covariates, regions, survey waves, missing values,
and people on blood-pressure medication.

Usage:
    python make-null-dataset.py [--n 3000] [--seed 20261010] [--out data/coffee-bp-null.csv]
Writes the CSV and a manifest JSON next to it (seed, rows, columns, sha256, truth).
The sha256 covers the file as written, so it depends on the platform's line endings
(CRLF on Windows, LF on Linux and macOS); the values are the same everywhere.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from null_survey import build


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--out", default="data/coffee-bp-null.csv", help="relative to the current folder")
    args = ap.parse_args()

    df = build(args.n, args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    manifest = {
        "file": out.name, "rows": len(df), "columns": list(df.columns), "seed": args.seed,
        "sha256": sha, "numpy": np.__version__, "pandas": pd.__version__,
        "truth": "coffee_cups_day, caffeine_mg_day and coffee_drinker are generated independently "
                 "of every other column: the true coffee effect on every outcome is exactly zero.",
    }
    out.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps({k: manifest[k] for k in ("file", "rows", "seed", "sha256")}))


if __name__ == "__main__":
    main()
