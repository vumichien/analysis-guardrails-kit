"""Summarize every specification tried (results_r*.csv) as a curve, not as a finding.

Writes curve_summary.json: the number of specifications, the median z (estimate / se),
the share significant at p < 0.05 by sign, and the range of z and of the estimates.

Usage: python .claude/skills/analysis-guardrails/scripts/summarize-curve.py [--workspace .]
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from guardrail_common import CURVE_SUMMARY, read_rows, round_files, to_float  # noqa: E402


def summarize(ws):
    files = round_files(ws)
    rows = [r for p in files for r in read_rows(p)]
    zs, ests, sig_pos, sig_neg = [], [], 0, 0
    for r in rows:
        est, se, p = to_float(r.get("estimate")), to_float(r.get("se")), to_float(r.get("pvalue"))
        if est is None or se is None or se <= 0:
            continue
        z = est / se
        zs.append(z)
        ests.append(est)
        if p is not None and p < 0.05:
            sig_pos += est > 0
            sig_neg += est < 0
    n = len(rows)
    return {
        "n_specs": n, "n_with_estimate_and_se": len(zs), "files": [p.name for p in files],
        "median_z": statistics.median(zs) if zs else None,
        "share_significant_positive": sig_pos / n if n else None,
        "share_significant_negative": sig_neg / n if n else None,
        "z_min": min(zs) if zs else None, "z_max": max(zs) if zs else None,
        "estimate_min": min(ests) if ests else None, "estimate_max": max(ests) if ests else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=".")
    args = ap.parse_args()
    ws = Path(args.workspace).resolve()
    out = summarize(ws)
    (ws / CURVE_SUMMARY).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out))


if __name__ == "__main__":
    main()
