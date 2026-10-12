"""Rerun each finished run's analysis_final.py outside the agent and check that it prints the headline.

Point it at one or more run folders. A run folder is either an agent's workspace (it holds
results_final.csv and analysis_final.py) or a folder with a workspace/ inside it, as
experiment/make-run-folders.py builds them (with metadata.json next to workspace/).

For each run: copy the workspace to a scratch folder, put the data file where the run read
it (guardrail.json's real_data, else data/survey.csv), point every absolute path to the
original workspace at the scratch copy, run `python analysis_final.py` with a timeout, and
check whether it prints results_final.csv's estimate.

The data comes from one of:
  --seed N        rebuild it with null_survey.build(--n, N), the same seed for every run;
  --data PATH     copy this file into every run;
  --manifest FILE take the seed from a manifest: make-null-dataset.py's (one seed for every
                  run) or make-run-folders.py's data-manifest.json (the seed of each run's
                  file index, read from its metadata.json).
With a manifest, the data file's sha256 must match it; a rebuilt file is written with the
platform's line endings first and, on a mismatch, with the other ones (the values are the
same; only the bytes differ). Without a manifest the hash is recorded, not checked.

A headline reproduces when the script exits 0 and prints results_final.csv's estimate: some
decimal number (a point or an exponent, at least two significant digits) equal to it to the
precision printed, or to 1e-6 relative when more digits are printed (guardrail_common.prints_number).

Sandbox. Where bubblewrap (bwrap) is installed, which means Linux or WSL, the rerun goes
inside it: a read-only root, a fresh /tmp, no network, and only the scratch folder writable.
Elsewhere (plain Windows, macOS) the rerun is NOT sandboxed: the script runs with your
account's access. Read analysis_final.py and what it calls before you run this there.

Usage:
  python tools/reproduce-headlines.py RUN [RUN ...] (--seed N | --data PATH | --manifest FILE)
         [--n 3000] [--original-root DIR] [--timeout 600] [--python PY] [--out FILE]
"""
import argparse
import csv
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "kit" / "skills" / "analysis-guardrails" / "scripts"))
from guardrail_common import prints_number  # noqa: E402
from null_survey import build  # noqa: E402


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def headline(ws):
    p = ws / "results_final.csv"
    if not p.exists():
        return None, "results_final.csv missing"
    with open(p, newline="", encoding="utf-8", errors="replace") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None, "results_final.csv has no rows"
    try:
        v = float(rows[0]["estimate"])
    except (KeyError, TypeError, ValueError):
        return None, "estimate is not a number"
    return (v if math.isfinite(v) else None), (None if len(rows) == 1 else f"{len(rows)} rows; first used")


def bwrap_prefix(home, scratch):
    # The fresh /tmp comes before the bind: bwrap mounts in order, and the temp home lives under /tmp.
    if not shutil.which("bwrap"):
        return [], False
    return ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
            "--bind", str(home), str(home), "--chdir", str(scratch), "--unshare-net", "--die-with-parent"], True


def clean_env(python, home):
    """A small environment built from scratch: nothing of yours beyond what Python needs to start."""
    env = {"PATH": os.pathsep.join([str(Path(python).parent), *(["/usr/bin", "/bin"] if os.name != "nt" else [])]),
           "HOME": str(home), "LANG": "C.UTF-8", "MPLBACKEND": "Agg"}
    if os.name == "nt":
        for k in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP"):
            if k in os.environ:
                env[k] = os.environ[k]
        if "SYSTEMROOT" in os.environ:
            env["PATH"] += os.pathsep + os.path.join(os.environ["SYSTEMROOT"], "System32")
    return env


def workspace_of(run):
    run = Path(run).resolve()
    return (run / "workspace", run) if (run / "workspace").is_dir() else (run, None)


def data_for(run_base, manifest, args):
    """(seed or None, expected sha256 or None, why-not or None) for one run."""
    if args.data:
        return None, (manifest or {}).get("sha256"), None
    if args.seed is not None:
        return args.seed, (None if "files" in manifest else manifest.get("sha256")), None
    if "files" in manifest:  # make-run-folders.py's data-manifest.json
        meta_p = run_base / "metadata.json" if run_base else None
        if not meta_p or not meta_p.exists():
            return None, None, "metadata.json missing, so the run's file index is unknown"
        index = json.loads(meta_p.read_text(encoding="utf-8"))["index"]
        f = next((f for f in manifest["files"] if f["index"] == index), None)
        if f is None:
            return None, None, f"file index {index} is not in the manifest"
        return f["data_seed"], f["data_sha256"], None
    return manifest["seed"], manifest.get("sha256"), None


def place_data(dest, seed, expected, args):
    """Write the data file; return (sha256, matched: True/False/None)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if args.data:
        shutil.copy(args.data, dest)
        sha = sha256(dest)
        return sha, (sha == expected) if expected else None
    df = build(args.n, seed)
    first = os.linesep
    for term in (first, "\r\n" if first == "\n" else "\n"):
        df.to_csv(dest, index=False, lineterminator=term)
        sha = sha256(dest)
        if not expected or sha == expected:
            break
    return sha, (sha == expected) if expected else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("runs", nargs="+", help="run folders (a workspace, or a folder holding workspace/)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--seed", type=int, help="rebuild the data with null_survey.build(--n, seed)")
    src.add_argument("--data", help="a data file to copy into every run")
    ap.add_argument("--manifest", help="make-null-dataset.py's manifest or make-run-folders.py's data-manifest.json")
    ap.add_argument("--n", type=int, default=3000, help="rows to rebuild (default 3000)")
    ap.add_argument("--original-root", help="the run root the agents ran in, when the run folders were copied "
                                            "elsewhere (default: the manifest's run_root)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--out", default="headline-reproduction.json")
    args = ap.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8")) if args.manifest else {}
    if args.seed is None and not args.data and not manifest:
        ap.error("give the data: --seed, --data or --manifest")
    if args.data and "files" in manifest:
        ap.error("--data takes make-null-dataset.py's manifest, not a batch data-manifest.json")
    original_root = args.original_root or manifest.get("run_root")
    if not shutil.which("bwrap"):
        print("bubblewrap not found: the reruns are NOT sandboxed (they run with your account's access).",
              file=sys.stderr)

    results = []
    for run in args.runs:
        ws, base = workspace_of(run)
        meta = json.loads((base / "metadata.json").read_text(encoding="utf-8")) if base and (
            base / "metadata.json").exists() else {}
        rec = {"run": str(Path(run)), "id": meta.get("id"), "arm": meta.get("arm"), "persona": meta.get("persona"),
               "index": meta.get("index")}
        est, note = headline(ws)
        rec.update({"headline_estimate": est, "note": note})
        if est is None or not (ws / "analysis_final.py").exists():
            rec.update({"reproduced": False, "why": note or "analysis_final.py missing"})
            results.append(rec)
            print(f"{rec['run']}: doesn't reproduce ({rec['why']})")
            continue
        seed, expected, why = data_for(base, manifest, args)
        if why:
            rec.update({"reproduced": False, "why": why})
            results.append(rec)
            print(f"{rec['run']}: doesn't reproduce ({why})")
            continue
        cfg_p = ws / "guardrail.json"
        data_rel = json.loads(cfg_p.read_text(encoding="utf-8"))["real_data"] if cfg_p.exists() else "data/survey.csv"
        with tempfile.TemporaryDirectory(prefix="agk-repro-") as tmp:
            scratch = Path(tmp) / "workspace"
            shutil.copytree(ws, scratch)
            sha, matched = place_data(scratch / data_rel, seed, expected, args)
            rec.update({"data": args.data or f"null_survey.build({args.n}, {seed})", "data_sha256": sha,
                        "data_hash_matches_manifest": matched})
            if matched is False:
                raise SystemExit(f"{rec['run']}: the data file does not match the manifest's sha256; "
                                 "check the seed, --n and the manifest.")
            originals = {str(ws)}
            if original_root and meta.get("id"):
                originals.add(f"{original_root}/{meta['id']}/workspace")
            for py in scratch.rglob("*.py"):
                text = py.read_text(encoding="utf-8", errors="replace")
                new = text
                for o in originals:
                    new = new.replace(o, str(scratch))
                if new != text:
                    py.write_text(new, encoding="utf-8")
            prefix, sandboxed = bwrap_prefix(tmp, scratch)
            try:
                p = subprocess.run(prefix + [args.python, "analysis_final.py"], cwd=scratch, capture_output=True,
                                   text=True, timeout=args.timeout, env=clean_env(args.python, tmp))
                ok = p.returncode == 0 and prints_number(p.stdout, est)
                rec.update({"exit": p.returncode, "reproduced": ok, "sandboxed": sandboxed,
                            "stdout_head": p.stdout[:400], "stderr_tail": p.stderr[-400:]})
            except subprocess.TimeoutExpired:
                rec.update({"reproduced": False, "why": f"timeout after {args.timeout} s", "sandboxed": sandboxed})
        results.append(rec)
        verdict = "reproduces" if rec["reproduced"] else "doesn't reproduce"
        print(f"{rec['run']}: {verdict}")

    out = {"ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "platform": platform.platform(),
           "python": platform.python_version(), "timeout_s": args.timeout,
           "sandbox": "bubblewrap, no network" if shutil.which("bwrap") else "none (bubblewrap not found)",
           "rule": "reproduced = exit 0 and some printed decimal number (a point or an exponent, at least 2 "
                   "significant digits) equals results_final.csv's estimate to the printed precision (1e-6 "
                   "relative when more digits are printed)",
           "runs": results, "reproduced": sum(r["reproduced"] for r in results), "n": len(results)}
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({"n": out["n"], "reproduced": out["reproduced"], "out": args.out}))


if __name__ == "__main__":
    main()
