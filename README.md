# Analysis guardrails kit

This is the companion code for the Towards Data Science article *How to Build a Harness for AI Data Analysis Agents* (link added when the article is published). An AI agent asked to analyse a data set can try dozens of defensible specifications and report the one that fits the hunch in its prompt, and a reviewer reading the final script can't see that search. The kit here is a Claude Code skill and three hooks that make the agent fix its plan on a blind copy of the data, lock it, run it once on the real file, and report everything else it tried as a curve. Next to it are the tools to grade that search (a specification curve and an m-value) and to rerun a reported headline outside the agent, plus the experiment harness I used to test all of it on a synthetic survey where the true effect is zero.

## The seven layers and where they live

| Layer | What it does | Files |
|---|---|---|
| 1. Isolation | The agent runs with `permissionMode: "dontAsk"`, an allowlist of tools, no web tools, an environment built from scratch, and Claude Code's OS sandbox (`failIfUnavailable: true`, no network, writes only to its workspace and `/tmp`). | `experiment/run-arms.mjs` (`buildEnv`, `sandboxFor`, `baseOptions`, `runOptions`), and `allowed_tools`, `disallowed_tools`, `sandbox` in `experiment/batch.template.json`. The kit's settings template holds hooks only; see [The sandbox](#the-sandbox) for what isolation means on each platform. |
| 2. Data gating | The agent builds and tests its analysis on a blind copy (the exposure columns shuffled together across rows), locks the plan by hash, and only then runs it once on the real file. | `kit/skills/analysis-guardrails/scripts/make-blind.py`, `lock-plan.py`, `run-locked.py` |
| 3. Advice | The skill tells the agent the order of work: read the setup, write the plan, lock, run once, explore, summarise the curve, report. | `kit/skills/analysis-guardrails/SKILL.md` |
| 4. Enforcement | Hooks refuse the tool calls that break the rules and won't let the agent stop until the end state is right. | `kit/hooks/guard-pre-tool.py`, `kit/hooks/ledger-post-tool.py`, `kit/hooks/stop-gate.py`, wired up by `kit/settings-enforce.template.json` or by `kit/skill-frontmatter-variant/SKILL.md` |
| 5. Grading the search | Every specification the agent tried becomes a curve, and the headline gets an m-value against a reference set of paths on the same data. | `kit/skills/analysis-guardrails/scripts/summarize-curve.py`, `tools/m_value.py` (and `tools/null_garden.py` for a garden of 576 simple specifications) |
| 6. Reproduction | The final script is rerun outside the agent, on a rebuilt copy of the data, and has to print the reported estimate. | `tools/reproduce-headlines.py` |
| 7. Convergence | Several independent analyses have to agree before you act on a result, and "we can't make a recommendation" is a valid answer. Not implemented here: the idea comes from PyMC Labs' [decision-lab](https://github.com/pymc-labs/decision-lab) (Apache-2.0), the article credits it, and no code from it is copied. | none |

## What's in the repo

```
kit/          the skill, its scripts, the hooks, the settings template, the frontmatter variant, the tests
tools/        the null survey generator, the garden of specifications, the m-value, headline reproduction
experiment/   the harness the article's tests ran on (a fork of AgenticBootstrap; see its LICENSE and NOTICE.txt)
```

No data files are kept. Every data set the article used is rebuilt from its seed.

## Install the guardrails into a Claude Code project

The skill and the hooks work on a workspace laid out like this:

```
your-project/
  guardrail.json          what the guardrails protect
  data/survey.csv         the real data (any name; guardrail.json says which)
  data/codebook.txt       what the columns mean (the skill tells the agent to read it)
  .claude/skills/analysis-guardrails/   the skill
  .claude/settings.json   the hooks
```

`guardrail.json` names the real file, the blind copy, the exposure columns to shuffle and the shuffle seed. `protected_files` is optional; it defaults to the six files the lock covers.

```json
{
  "real_data": "data/survey.csv",
  "blind_data": "data/blind.csv",
  "exposure_columns": ["coffee_cups_day", "caffeine_mg_day", "coffee_drinker"],
  "blind_seed": 2001
}
```

The SKILL.md text names `data/blind.csv` and `data/codebook.txt`, so keep those two paths or edit your copy of the skill. With no `guardrail.json` in the workspace, the hooks allow everything and the Stop gate doesn't apply.

**Option 1: project settings.**

1. Copy `kit/skills/analysis-guardrails/` into `your-project/.claude/skills/analysis-guardrails/`.
2. Copy the three files in `kit/hooks/` into `your-project/.claude/hooks/`. Each hook finds `guardrail_common.py` in its own folder or in `../skills/analysis-guardrails/scripts/`, so this layout works as is.
3. Merge `kit/settings-enforce.template.json` into `your-project/.claude/settings.json` and replace its four placeholders by hand: `{{PY}}` with a Python 3 interpreter, `{{HOOKS}}` with the absolute path of the hooks folder, `{{WS}}` with the absolute path of the workspace, and `{{LOG}}` with a folder for the hook logs. Put the log folder outside the workspace if you don't want the agent to read it, and create it before you start an agent: the hooks append to it and don't create it, and a missing folder makes the pre-tool hook fail, which `onFailure: "block"` turns into a refusal of every tool call.

The template registers `guard-pre-tool.py` on every tool call (`PreToolUse`, matcher `"*"`, `onFailure: "block"`), `ledger-post-tool.py` after every call (`PostToolUse`), and `stop-gate.py` when the agent tries to finish (`Stop`). Each has a 30-second timeout. In the experiment, `make-run-folders.py` fills the placeholders for you and keeps the hooks outside the agent's working folder.

**Option 2: hooks in the skill's frontmatter.** Use `kit/skill-frontmatter-variant/SKILL.md` instead of the plain SKILL.md. It has the same body and the same three hooks in its frontmatter, with the same placeholders to fill. The catch: frontmatter hooks only start once the skill is invoked. If the agent never invokes the skill, nothing is enforced. Starting the task with `/analysis-guardrails`, as the experiment's guardrail arms do, invokes it.

Whichever option you pick, check that the skill actually loads. The original AgenticBootstrap runner asks for `@anthropic-ai/claude-agent-sdk` `^0.1.0`, which resolves to 0.1.77 and loads no project skills or hooks; the harness here pins 0.3.296. A guardrail that never loads isn't there.

### What the hooks check

`guard-pre-tool.py` reads each tool call and refuses three things (in `--mode enforce`; `--mode log` only records the verdict):

- **T1**: touching the real data file before the plan is locked (a Read, a Grep over its folder, a shell command that names it, or a Python script that names it);
- **T2**: changing a locked file after the lock (`plan.json`, `analysis_locked.py`, `plan.lock`, `locked_result.json`, `results_final.csv`, `analysis_final.py`);
- **T3**: editing the guardrail's own configuration (`.claude/` or `guardrail.json`).

`stop-gate.py` checks six things on the files before the agent may stop: a valid lock (G1), a locked result that records the lock's hashes (G2), a final row equal to the locked result (G3), an untouched `analysis_final.py` (G4), a curve summary that counts every logged specification (G5), and a report that states the locked estimate and the number of specifications (G6). It never runs the analysis, so it can't time out on a slow model. `ledger-post-tool.py` appends every tool call to a log and always exits 0.

### Hook caveats

- **Hooks fail open by default.** If a hook crashes or times out, Claude Code carries on. `onFailure: "block"` turns a hook failure into a block, but it needs Claude Code 2.1.295 or later and has no effect on Stop hooks.
- **Stop hooks give up.** After 8 consecutive continuations, Claude Code lets the agent stop anyway.
- **A wrong script path blocks everything.** If `{{HOOKS}}` points at the wrong folder, Python exits with code 2 because it can't find the file, and Claude Code reads exit code 2 from a PreToolUse hook as "block". Every tool call is refused. (`make-run-folders.py`'s `missing-hook-*` smoke variant reproduces this on purpose.) Test the hook command by hand before you start an agent.
- **Hooks match file names, not intentions.** A file name assembled at run time, or a script that reads the real path out of `guardrail.json`, slips past the call-level checks. The end-state checks and an independent rerun catch some of what they miss. They're built for a cooperative agent that drifts, not for an adversary.

## The sandbox

Claude Code's OS sandbox runs on macOS, Linux and WSL2. On plain Windows there isn't one: the agent's Python can read whatever your account can read. If you run agents on Windows, run them in WSL, or treat everything your account can reach as visible to them. `run-arms.mjs` asks for the sandbox with `failIfUnavailable: true`, so it refuses to start rather than quietly running without it.

## Run the tests

```
python -m pip install -r requirements.txt
python -m pytest kit/tests -q
```

The tests replay tool calls through the pre-tool hook in both modes, run each Stop-gate check against a finished workspace and a broken one, and exercise the lock, run, reproduce and blind-copy scripts. They rebuild the pilot null file (seed 20261010) in a temporary folder, so they need nothing else. They take about two minutes.

## Generate a null survey

```
python tools/make-null-dataset.py --seed 20261010 --out data/coffee-bp-null.csv
```

This writes a synthetic "coffee and blood pressure" survey of 3,000 rows and 22 columns, plus a manifest with the seed and the file's sha256. Every coffee column is generated independently of every other column, so the true coffee effect on every outcome is exactly zero: any "significant" coffee effect on this file is a false positive. The file still has the forks real survey data has: three ways to measure coffee, four outcomes, a dozen covariates, regions, survey waves, missing values and people on blood-pressure medication.

The sha256 depends on line endings (CRLF on Windows, LF on Linux and macOS); the values don't. The article's files were built with numpy 2.5.3 and pandas 3.0.6, and other versions may draw different numbers from the same seed.

## Compute an m-value

The m-value of a reported result is twice the share of reference paths at least as extreme in the same direction, capped at 1 (Miao, Pritchard & Zou, arXiv:2607.01507). A small m-value means the reported result sits in a tail of what other defensible analyses of the same data found. It's relative to the reference set you give it, so say what that set is.

```python
import sys
sys.path.insert(0, "tools")
from m_value import m_value
from null_garden import sweep
from null_survey import build

garden = sweep(build(3000, 20261010))   # 576 specifications, one OLS fit each
print(m_value(2.1, garden["z"].tolist()))
```

## Reproduce a headline

Point `tools/reproduce-headlines.py` at one or more finished run folders. A run folder is an agent's workspace (it holds `results_final.csv` and `analysis_final.py`) or a folder with a `workspace/` inside it. Tell it where the data comes from:

```
python tools/reproduce-headlines.py path/to/run --seed 20261010
python tools/reproduce-headlines.py path/to/run --data data/coffee-bp-null.csv --manifest data/coffee-bp-null.manifest.json
python tools/reproduce-headlines.py experiment/results/agent-runs/<batch>/*/*/run_* --manifest experiment/results/agent-runs/<batch>/data-manifest.json
```

For each run it copies the workspace to a scratch folder, puts the data where the run read it, rewrites absolute paths to the original workspace, runs `python analysis_final.py` with a timeout, and checks that the output prints the estimate in `results_final.csv` to the precision printed. It reports "reproduces" or "doesn't reproduce" per run and writes the details to `headline-reproduction.json` (change it with `--out`). With a manifest, the data file has to match the manifest's sha256 first.

Where bubblewrap is installed, which means Linux or WSL, the rerun goes inside it: read-only root, fresh `/tmp`, no network. On Windows and macOS the rerun isn't sandboxed and runs with your account's access, and the script says so. Read the script before you rerun it there.

## Run the experiment

This is the harness the article's tests ran on: three arms (A, no guardrail; B, the skill; C, the skill and the hooks), two personas (believer and skeptic), five null files, ten rounds of exploration per run. You need:

- Linux or WSL (the sandbox and the run environment assume it);
- Node 18 or later, then `npm install` inside `experiment/`;
- a Python venv with numpy, pandas and statsmodels for the agents' own scripts;
- a model endpoint Claude Code can talk to, and a key for it.

The key reaches Claude Code only through an apiKeyHelper script: `llm-key-helper.py` prints one key from a `.env` file and nothing else (`anthropic-key-helper.py` does the same for `ANTHROPIC_API_KEY` and has to sit next to it). Install the helper outside every run folder. Never put the key in an environment variable, because Claude Code's Bash tool passes its environment to the agent's commands.

1. Copy `experiment/batch.template.json` to a config of your own and fill in every `<...>` value: the model id, the endpoint, the venv's Python, the helper's path and your `.env` path.
2. Build the run folders: `python experiment/make-run-folders.py --config /abs/path/batch.json`. Keep the run root outside any project tree (the template uses `~/gs-runs/<batch>`). Claude Code loads `CLAUDE.md` files from parent folders, so a run folder inside a project would feed that project's instructions to the agents. The script refuses a run root inside this repository or under a folder that holds a `CLAUDE.md`.
3. Check without calling the model: `node experiment/run-arms.mjs --config /abs/path/batch.json --dry-run` prints one prompt and the options per arm.
4. Check the model answers: `node experiment/run-arms.mjs --config /abs/path/batch.json --ping`.
5. Run the batch: `node experiment/run-arms.mjs --config /abs/path/batch.json`. It resumes where it stopped, and an infrastructure failure gets one rerun from scratch.
6. Copy the outputs, after a scan for your key: `python experiment/collect-runs.py --run-root ~/gs-runs/<batch> --batch <batch> --env-file /abs/path/.env`.
7. Rerun the headlines (see above), writing to `experiment/results/headline-reproduction.json`, then `python experiment/analyze-agent-runs.py --batch <batch>`.

Every agent runs with `permissionMode: "dontAsk"` and the config's allowlist. Never switch it to `bypassPermissions`.

## What this doesn't protect against

- **An adversary.** The hooks read tool calls, not intentions. An agent that sets out to get around them can, for example by building the data file's name at run time. The checks are for a cooperative agent that drifts toward the answer its prompt hints at.
- **A bad judgment call inside one analysis.** The lock fixes which analysis gets reported; it doesn't make that analysis a good one. A poorly chosen outcome, control set or model is just as locked as a good one.

## Licences

The code in this repository is MIT-licensed (`LICENSE`, Copyright (c) 2026 Chien Vu Minh). `experiment/` is a fork of [AgenticBootstrap](https://github.com/jmiao24/AgenticBootstrap) by Jiacheng Miao, also MIT: its licence is kept in `experiment/LICENSE`, and `experiment/NOTICE.txt` lists what was copied verbatim (the persona and round prompts among them) and every change made to the runner.
