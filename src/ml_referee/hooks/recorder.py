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

_REDIRECT = re.compile(
    r"(?:(?<=\s)|^)(?:\d*|&)(?:>{1,2}|<{1,3})\s*&?\s*[^\s;&|'\")]+",
)
_WRAPPER = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*=(?:'[^']*'|\"[^\"]*\"|\S*)"
    r"|(?:\S*/)?nice(?:\s+-n\s+[+-]?\d+|\s+-\d+)?"
    r"|(?:\S*/)?env(?:\s+(?:-i|-0|--ignore-environment|(?:-u|--unset)\s+\S+))*"
    r"|(?:\S*/)?time(?:\s+-p)?"
    r"|(?:\S*/)?(?:nohup|setsid)|exec|command"
    r"|(?:\S*/)?(?:bash|sh)\s+-[a-z]*c"
    r"|(?:poetry|pipenv)\s+run|uv\s+run)(?:\s+|$)",
)
_UV_VALUE_FLAGS = {
    "--with", "--with-requirements", "--python", "-p", "--directory", "--project",
    "--env-file", "--extra", "--group", "--package", "--no-group", "--only-group",
    "--with-editable", "--python-preference", "--index", "--default-index",
    "--find-links", "-f", "--config-file", "-c",
}
_PYTHON = re.compile(r"^python(?:\d+(?:\.\d+)*)?$")
_PY_FLAGS = re.compile(r"^-[uBOIsSE]+$")


def _drop_uv_flags(segment: str, before: str) -> str:
    """Drop ``uv run`` flags (and their values) so the command itself leads."""
    if not re.match(r"^uv\s+run(?:\s|$)", before):
        return segment
    tokens = segment.split()
    while tokens and tokens[0].startswith("-"):
        flag = tokens.pop(0)
        if flag == "--":
            break
        if flag in _UV_VALUE_FLAGS and tokens and "=" not in flag:
            tokens.pop(0)
    return " ".join(tokens)


def _reduce_interpreter(segment: str) -> str:
    """Reduce an interpreter path/version to ``python`` and drop its safe flags."""
    tokens = segment.split()
    if not tokens:
        return segment
    base = tokens[0].rsplit("/", 1)[-1]
    if not _PYTHON.match(base):
        return segment
    rest = tokens[1:]
    while rest and _PY_FLAGS.match(rest[0]):
        rest.pop(0)
    return " ".join(["python", *rest])


def _unquote(segment: str) -> str:
    """Remove one pair of matching outer quotes, or unbalanced edge quotes."""
    if len(segment) > 1 and segment[0] == segment[-1] and segment[0] in "'\"":
        return segment[1:-1]
    quotes = segment.count("'") + segment.count('"')
    return segment.strip("'\"") if quotes == 1 else segment


def _segments(command: str) -> list[str]:
    """Normalize command segments and remove leading execution wrappers.

    Redirections are removed, the command is split on ``&& || ; | &`` and
    newlines, then each segment loses leading wrappers (``VAR=value``, ``env``,
    ``time``, ``nohup``, ``uv run``, ``poetry run`` ...) and its interpreter is
    reduced to ``python``.
    """
    segments = []
    command = _REDIRECT.sub(" ", command)
    # shortcut: separators inside quotes also split; use shell parsing if needed.
    for raw_segment in re.split(r";|&&|\|\||\||&|\n", command):
        segment = _unquote(" ".join(raw_segment.split()))
        segment = segment.lstrip("({ ").rstrip("}) ")
        while segment:
            match = _WRAPPER.match(segment)
            if match is None:
                break
            stripped = _unquote(
                _drop_uv_flags(segment[match.end():], match.group(0)),
            ).lstrip("({ ")
            if stripped == segment:
                break
            segment = stripped
        segments.append(_reduce_interpreter(segment))
    return segments


def record(event: dict[str, object], cfg: Config, ledger: Ledger) -> Record | None:
    """Append a trial record when the event is a matching Bash command."""
    inputs = event.get("tool_input")
    if event.get("tool_name") != "Bash" or not isinstance(inputs, dict):
        return None
    command = inputs.get("command")
    if not isinstance(command, str) or not any(
        fnmatchcase(candidate, pattern)
        or fnmatchcase(candidate, pattern + " *")
        or candidate == pattern
        or candidate.startswith(pattern + " ")
        for candidate in (" ".join(command.split()), *_segments(command))
        for pattern in cfg.trial_patterns
    ):
        return None
    if "exit_code" in event:  # canonical event
        exit_code = event["exit_code"]
        canonical_failed = event.get("failed")
        if not isinstance(canonical_failed, bool):
            canonical_failed = None
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
        "failed": (
            True
            if canonical_failed is True
            or event.get("hook_event_name") == "PostToolUseFailure"
            or (exit_code is not None and exit_code != 0)
            else False
            if canonical_failed is False or exit_code == 0
            else None
        ),
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
        if harness == "claude":
            events = [raw]  # keep tool_response exit_code/failed handling
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
