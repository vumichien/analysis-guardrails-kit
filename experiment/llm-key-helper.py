"""apiKeyHelper for Claude Code: print the gateway token from a .env file, and nothing else.

Claude Code runs this command when it needs a credential and reads the token from stdout,
so the token never sits in an environment variable that the agent's own commands inherit.
Install it outside every run folder. On a failure it names the problem on stderr, never a value.

Usage (as the apiKeyHelper command): <python> llm-key-helper.py <path to .env> [KEY_NAME]
"""
import sys
from pathlib import Path


def read_key(env_path, name):
    for raw in Path(env_path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() != name:
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        return v
    return None


def main():
    if len(sys.argv) < 2:
        print("usage: llm-key-helper.py <.env> [KEY_NAME]", file=sys.stderr)
        return 2
    name = sys.argv[2] if len(sys.argv) > 2 else "LLM_API_KEY"
    try:
        value = read_key(sys.argv[1], name)
    except OSError as e:
        print(f"cannot read the env file ({e.__class__.__name__})", file=sys.stderr)
        return 1
    if not value:
        print(f"{name} not found in the env file", file=sys.stderr)
        return 1
    sys.stdout.write(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
