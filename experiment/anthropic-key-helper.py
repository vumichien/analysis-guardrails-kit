"""apiKeyHelper for the Anthropic API route: print ANTHROPIC_API_KEY from a .env file, and nothing else.

The same parser as llm-key-helper.py (which must sit in the same folder), with another key name,
so run-arms.mjs needs no change: its apiKeyHelper command is "<python> <key_helper> <.env>".
Install it outside every run folder, next to llm-key-helper.py. A failure names the problem on
stderr, never a value.

Usage (as the apiKeyHelper command): <python> anthropic-key-helper.py <path to .env>
"""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("llm_key_helper", Path(__file__).with_name("llm-key-helper.py"))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

if __name__ == "__main__":
    sys.argv = sys.argv[:2] + ["ANTHROPIC_API_KEY"]
    sys.exit(helper.main())
