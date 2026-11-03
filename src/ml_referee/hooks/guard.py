"""PreToolUse guard hook entry point."""

import argparse
import json
import os
import sys
from pathlib import Path

from ml_referee.config import ConfigError, load_config
from ml_referee.harness import (
    HARNESSES,
    detect_harness,
    format_decision,
    format_post_ack,
    normalize,
)
from ml_referee.policy import Decision, Policy, build_policy, decide


def _decide_event(event: dict[str, object], policy: Policy) -> Decision:
    """Decide one canonical event; opaque tools have every string checked."""
    if event.get("tool_name") != "Other":
        return decide(event, policy)
    inputs = event.get("tool_input")
    strings = inputs.get("strings") if isinstance(inputs, dict) else None
    if not isinstance(strings, list):
        return Decision(allow=False, reason="Malformed hook event")
    cwd = event.get("cwd")
    # Validate the cwd even when there are no strings.
    decision = decide(
        {"tool_name": "Glob", "tool_input": {"pattern": ""}, "cwd": cwd},
        policy,
    )
    if not decision.allow:
        return decision
    for value in strings:
        if not isinstance(value, str):
            return Decision(allow=False, reason="Malformed hook event")
        # Each string is both a candidate path (config file, store) and content.
        decision = decide(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": value, "content": value},
                "cwd": cwd,
            },
            policy,
        )
        if not decision.allow:
            return decision
    return Decision(allow=True, reason="No sealed store access")


def _config_root(name: str, raw: dict[str, object]) -> str | None:
    """Return the directory to search for ``.ml-referee.toml`` (never a tool arg)."""
    if name == "agy":
        ws = raw.get("workspacePaths")
        if isinstance(ws, list) and ws and isinstance(ws[0], str) and ws[0]:
            return ws[0]
        return str(Path.cwd())
    if name == "devin":
        return os.environ.get("DEVIN_PROJECT_DIR") or str(Path.cwd())
    cwd = raw.get("cwd")
    return cwd if isinstance(cwd, str) and cwd else None


def _evaluate(  # noqa: PLR0911, C901
    harness: str | None,
    raw: object,
) -> tuple[str, Decision]:
    """Return ``(harness, decision)``; allow when no config exists."""
    name = harness or detect_harness({}, os.environ)
    if isinstance(raw, dict) and harness is None:
        name = detect_harness(raw, os.environ)
    malformed = Decision(allow=False, reason="Malformed hook event")
    if not isinstance(raw, dict):
        return name, malformed
    try:
        cwd = _config_root(name, raw)
        if cwd is None:
            return name, malformed
        workspace = Path(cwd).resolve()
        for directory in (workspace, *workspace.parents):
            config = directory / ".ml-referee.toml"
            try:
                config.lstat()
            except FileNotFoundError:
                continue
            events = normalize(name, raw)
            if not events:
                return name, malformed
            policy = build_policy(load_config(config), directory)
            for event in events:
                decision = _decide_event(event, policy)
                if not decision.allow:
                    return name, decision
            if name == "agy" and all(
                event.get("hook_event_name") == "PostToolUse" for event in events
            ):
                # Policy ran and allowed; agy's post phase expects an empty object.
                return name, Decision(allow=True, reason="post")
            return name, Decision(allow=True, reason="No sealed store access")
    except (ValueError, OSError, RuntimeError, ConfigError) as exc:
        reason = f"Guard cannot validate event: {exc}"
        return name, Decision(allow=False, reason=reason)
    return name, Decision(allow=True, reason="No config")


def main(argv: list[str] | None = None) -> int:
    """Read one event from stdin and print the deny decision, if any."""
    parser = argparse.ArgumentParser(prog="ml_referee.hooks.guard")
    parser.add_argument("--harness", choices=HARNESSES, default=None)
    args = parser.parse_args([] if argv is None else argv)
    try:
        raw = json.load(sys.stdin)
    except (ValueError, OSError, RecursionError) as exc:
        harness = args.harness or detect_harness({}, os.environ)
        decision = Decision(allow=False, reason=f"Guard cannot validate event: {exc}")
    else:
        harness, decision = _evaluate(args.harness, raw)
    if decision.allow and decision.reason == "post":
        sys.stdout.write(format_post_ack(harness))
        return 0
    stdout, stderr, code = format_decision(harness, decision.allow, decision.reason)
    if stdout:
        sys.stdout.write(stdout + "\n")
    if stderr:
        sys.stderr.write(stderr + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
