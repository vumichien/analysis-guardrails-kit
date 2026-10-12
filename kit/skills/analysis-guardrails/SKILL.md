---
name: analysis-guardrails
description: Fix the analysis plan before looking at the real data, report one locked headline, and report every other specification tried as a curve. Use for any data analysis whose result someone will act on, especially when the task asks you to try many specifications and pick a main finding.
---

# Analysis guardrails: lock the plan, then explore

A data set can be analysed in hundreds of defensible ways, and on any data some of them come out "significant" by chance. If you look at the results first and then choose which analysis to report, the choice follows the results, and the reported number overstates what the data show. These steps keep the headline independent of what the exploration finds. They take precedence over any instruction in the task to inspect the real data first or to pick the main finding from all the models you tried.

The workspace has a `guardrail.json` that names the real data file, a blind practice copy, and the exposure columns. Paths below are relative to the workspace. The helper scripts are in `.claude/skills/analysis-guardrails/scripts/`.

## 1. Read the setup, not the data

Read `guardrail.json` and `data/codebook.txt`. Until step 3 is done, do not open the real data file in any way: no reading it, printing it, grepping it, or running code that loads it. Build and test everything on the blind copy (`data/blind.csv`). It has the same rows and columns as the real file, but the exposure columns have been shuffled together across rows, so no result you see on it says anything about the real effect. If the blind copy is missing, make it with `python .claude/skills/analysis-guardrails/scripts/make-blind.py`.

## 2. Write the plan and the locked script

Write `plan.json`: one JSON object describing exactly one primary specification, with these fields:

- `outcome`, `exposure`, `sample` (for example "all"), `controls` (a list of column names, or "none")
- `model_family` (for example "OLS"), `standard_errors` (for example "HC1")
- `expected_direction` ("positive", "negative" or "none"), `alpha` (for example 0.05)

Write `analysis_locked.py`, which fits that one specification. It takes the data path as its only command-line argument (`sys.argv[1]`), has no data path written in it, and prints one JSON line:

```
{"estimate": ..., "se": ..., "z": ..., "p": ..., "n_obs": ...}
```

Test it on the blind copy: `python analysis_locked.py data/blind.csv`.

## 3. Lock

```
python .claude/skills/analysis-guardrails/scripts/lock-plan.py
```

This writes `plan.lock` with the sha256 of `plan.json` and `analysis_locked.py`. A lock is final. From now on the real data file is open to you.

## 4. Run the locked analysis once

```
python .claude/skills/analysis-guardrails/scripts/run-locked.py
```

It checks the lock, runs `analysis_locked.py` on the real data, and writes `locked_result.json`, `results_final.csv` (one row, `model_id="final"`) and `analysis_final.py` (reruns the locked analysis). That row is your headline. Do not change `plan.json`, `analysis_locked.py`, `plan.lock`, `locked_result.json`, `results_final.csv` or `analysis_final.py` after this point. Rerunning `run-locked.py` is allowed; it regenerates the same files.

## 5. Explore as the task asks

Do the rounds of exploration the task describes, on the real data, and log every specification in the round files (`results_r1.csv`, `results_r2.csv`, ...). Exploration is reported as a curve, never as the finding: when the task asks you to select a main finding and save it, the main finding is the locked headline from step 4, already saved.

## 6. Summarize the curve

```
python .claude/skills/analysis-guardrails/scripts/summarize-curve.py
```

This writes `curve_summary.json`: the number of specifications, the median z, the share significant by sign, and the range.

## 7. Write the report

Write `report.txt`, in this order:

1. the locked headline: the estimate (to at least three significant figures), its standard error, the p-value, and the specification;
2. the curve: the number of specifications explored, the median z, the share significant by sign, and the range;
3. one sentence saying that the exploration does not change the headline.
