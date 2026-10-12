"""Copy a batch's run outputs into results/agent-runs/<batch>/ (next to this script), after a secret scan.

For every run in <run root>/runs.json: the workspace files the agent wrote (round files,
scripts, plan, lock, results, curve, report, .claude/settings.json) and the whole log/
folder, plus metadata.json. Never copied: data/ (the data CSVs are rebuilt from their
seeds; see data-manifest.json), the skill copy under .claude/skills/ (hashed in the
manifest), the pristine workspace copy, and any other CSV of 1,000 lines or more (a copy of
the data). Markdown files the agents wrote are renamed *.md.txt, so a copied run never reads
as instructions to a Claude Code session opened on the results. The key hash in LiteLLM's rate-limit messages is replaced
by "<hash>" in the copies.

Before copying anything, every candidate file is scanned for the gateway token. The token
is loaded inside this process from the .env file and never printed; on a hit the script
prints only the file names and stops.

Usage: python collect-runs.py --run-root ~/gs-runs/b1 --batch b1 --env-file <path to .env> [--key LLM_API_KEY]
"""
import argparse
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# LiteLLM's 429 text names the key by its sha256: not the token, but a stable id of a live key.
KEY_HASH = re.compile(rb"api_key: [0-9a-f]{16,}")


def load_key(env_file, name):
    """The token, read inside this process by the same parser the apiKeyHelper uses."""
    spec = importlib.util.spec_from_file_location("keyhelper", HERE / "llm-key-helper.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.read_key(env_file, name)


def candidates(run):
    ws, log = Path(run["workspace"]), Path(run["log"])
    base = ws.parent
    out, skipped = [], []
    for p in sorted(ws.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ws)
        if rel.parts[0] == "data" or rel.parts[:2] == (".claude", "skills") or "__pycache__" in rel.parts:
            continue
        if p.suffix == ".csv" and not p.name.startswith("results_"):
            with open(p, "rb") as f:
                if sum(1 for _ in f) >= 1000:
                    skipped.append(str(Path("workspace") / rel))
                    continue
        out.append((p, Path("workspace") / rel))
    for p in sorted(log.rglob("*")):
        if p.is_file():
            out.append((p, Path("log") / p.relative_to(log)))
    if (base / "metadata.json").exists():
        out.append((base / "metadata.json", Path("metadata.json")))
    return out, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", required=True)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--env-file", required=True)
    ap.add_argument("--key", default="LLM_API_KEY")
    ap.add_argument("--dest", default=None, help="default: results/agent-runs/<batch> (results/smoke for smoke)")
    args = ap.parse_args()
    root = Path(args.run_root).expanduser()
    runs = json.loads((root / "runs.json").read_text(encoding="utf-8"))["runs"]
    dest = Path(args.dest) if args.dest else HERE / "results" / "agent-runs" / args.batch

    token = load_key(args.env_file, args.key)
    if not token:
        raise SystemExit(f"{args.key} did not load; refusing to copy without the scan.")
    needle = token.encode()
    plan, hits, skipped_all = [], [], {}
    for run in runs:
        files, skipped = candidates(run)
        skipped_all[run["id"]] = skipped
        for src, rel in files:
            if needle in src.read_bytes():
                hits.append(str(src))
            plan.append((run["id"], src, rel))
    del token, needle
    if hits:
        print("The gateway token was found in these files; nothing was copied:")
        print("\n".join(hits))
        return 1

    renamed, scrubbed = [], []
    for run_id, src, rel in plan:
        if rel.suffix.lower() == ".md":
            rel = rel.with_name(rel.name + ".txt")
            renamed.append(str(Path(run_id) / rel))
        out = dest / run_id / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        clean = KEY_HASH.sub(b"api_key: <hash>", data)
        if clean != data:
            scrubbed.append(str(Path(run_id) / rel))
            out.write_bytes(clean)
            shutil.copystat(src, out)
        else:
            shutil.copy2(src, out)
    (dest / "collect-manifest.json").write_text(json.dumps({
        "run_root": str(root), "runs": len(runs), "files_copied": len(plan), "token_scan": "clean",
        "skipped_large_csv": skipped_all, "markdown_renamed": renamed, "key_hash_scrubbed": scrubbed},
        indent=1), encoding="utf-8")
    print(json.dumps({"dest": str(dest), "runs": len(runs), "files": len(plan), "token_scan": "clean"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
