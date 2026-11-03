"""PostToolUse hook that records trial commands in the ledger."""

import argparse
import hashlib
import json
import os
import re
import sys
from fnmatch import fnmatchcase
from pathlib import Path

from ml_referee.config import Config, find_config, load_config, store_dir
from ml_referee.harness import HARNESSES, detect_harness, normalize
from ml_referee.ledger import Ledger, Record


def _segments(command: str) -> list[str]:
    """Normalize command segments and remove leading execution wrappers."""
    segments = []
    # shortcut: separators inside quotes also split; use shell parsing if needed.
    for raw_segment in re.split(r";|&&|\|\||\||&|\n", command):
        segment = " ".join(raw_segment.split()).strip("'\"")
        while segment:
            stripped = re.sub(
                r"^(?:[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|\"[^\"]*\"|\S*)"
                r"|nice(?:\s+-n\s+[+-]?\d+)?"
                r"|nohup|env|time|setsid|exec|command|(?:bash|sh)\s+-c)(?:\s+|$)",
                "",
                segment,
                count=1,
            ).strip("'\"")
            if stripped == segment:
                break
            segment = stripped
        segments.append(segment)
    return segments


def record(event: dict[str, object], cfg: Config, ledger: Ledger) -> Record | None:
    """Append a trial record when the event is a matching Bash command."""
    inputs = event.get("tool_input")
    if event.get("tool_name") != "Bash" or not isinstance(inputs, dict):
        return None
    command = inputs.get("command")
    if not isinstance(command, str) or not any(
        fnmatchcase(segment, pattern)
        or fnmatchcase(segment, pattern + " *")
        or segment == pattern
        or segment.startswith(pattern + " ")
        for segment in _segments(command)
        for pattern in cfg.trial_patterns
    ):
        return None
    if "exit_code" in event:  # canonical event
        exit_code = event["exit_code"]
        canonical_failed = event.get("failed") is True
    else:
        response = event.get("tool_response")
        exit_code = response.get("exit_code") if isinstance(response, dict) else None
        canonical_failed = False
    try:
        with cfg.train_path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError:
        digest = None
    return ledger.append("trial", {
        "command": command,
        "exit_code": exit_code,
        "failed": canonical_failed
        or event.get("hook_event_name") == "PostToolUseFailure"
        or (exit_code is not None and exit_code != 0),
        "train_sha256": digest,
    })


def main(argv: list[str] | None = None) -> int:
    """Read one hook event from stdin and record it. Never raises after parsing."""
    parser = argparse.ArgumentParser(prog="ml_referee.hooks.recorder")
    parser.add_argument("--harness", choices=HARNESSES, default=None)
    args = parser.parse_args([] if argv is None else argv)
    try:
        raw = json.load(sys.stdin)
        if not isinstance(raw, dict):
            return 0
        harness = args.harness or detect_harness(raw, os.environ)
        events = normalize(harness, raw)
        if not events or not isinstance(events[0].get("cwd"), str):
            return 0
        config = find_config(Path(str(events[0]["cwd"])))
        if config is not None:
            cfg = load_config(config)
            if (store_dir(cfg) / "manifest.json").exists():
                for event in events:
                    record(event, cfg, Ledger(store_dir(cfg) / "ledger.jsonl"))
    except BaseException as exc:  # noqa: BLE001
        # Recording must never interrupt the caller, even on malformed input.
        try:
            sys.stderr.write(f"ml-referee recorder: {exc}\n")
        except BaseException:  # noqa: BLE001
            # A broken diagnostic stream must not interrupt the caller either.
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
