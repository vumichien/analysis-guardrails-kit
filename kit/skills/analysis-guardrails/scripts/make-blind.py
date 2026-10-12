"""Make the blind practice copy of the data: the exposure columns shuffled together.

Reads guardrail.json for the real and blind paths, the exposure columns and the seed.
The exposure columns are moved to other rows as one block, so every column keeps its
values (and the exposure columns keep their relationship to each other), while any link
between the exposure and everything else is broken. Build and test the analysis on this
copy; open the real file only after the plan is locked.

Prints only what it did (rows, columns shuffled, seed, output path), nothing about results.

Usage: python .claude/skills/analysis-guardrails/scripts/make-blind.py [--workspace .]
"""
import argparse
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from guardrail_common import blind_path, load_config, real_path  # noqa: E402


def make_blind(real, blind, columns, seed):
    with open(real, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    idx = [header.index(c) for c in columns]
    order = list(range(len(rows)))
    random.Random(seed).shuffle(order)
    block = [[rows[j][i] for i in idx] for j in order]
    for row, values in zip(rows, block):
        for i, v in zip(idx, values):
            row[i] = v
    Path(blind).parent.mkdir(parents=True, exist_ok=True)
    with open(blind, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=".")
    args = ap.parse_args()
    ws = Path(args.workspace).resolve()
    cfg = load_config(ws)
    if cfg is None:
        raise SystemExit("No guardrail.json in this workspace.")
    out = blind_path(ws, cfg)
    n = make_blind(real_path(ws, cfg), out, cfg["exposure_columns"], cfg["blind_seed"])
    print(json.dumps({"rows": n, "shuffled_together": cfg["exposure_columns"], "seed": cfg["blind_seed"],
                      "out": cfg["blind_data"]}))


if __name__ == "__main__":
    main()
