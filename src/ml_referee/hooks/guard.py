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
    for value in strings:
        if not isinstance(value, str):
            return Decision(allow=False, reason="Malformed hook event")
        decision = decide(
            {
                "tool_name": "Grep",
                "tool_input": {"pattern": value},
                "cwd": event.get("cwd"),
            },
            policy,
        )
        if not decision.allow:
            return decision
    return Decision(allow=True, reason="No sealed store access")


def _raw_cwd(name: str, raw: dict[str, object]) -> str | None:
    """Resolve the workspace directory from a raw payload before normalizing."""
    if name == "agy":
        default = str(Path.cwd())
        ws = raw.get("workspacePaths")
        if isinstance(ws, list) and ws and isinstance(ws[0], str):
            default = ws[0]
        call = raw.get("toolCall")
        args = call.get("args") if isinstance(call, dict) else None
        cwd_arg = args.get("Cwd") if isinstance(args, dict) else None
        return cwd_arg if isinstance(cwd_arg, str) and cwd_arg else default
    if name == "devin":
        return os.environ.get("DEVIN_PROJECT_DIR") or str(Path.cwd())
    cwd = raw.get("cwd")
    return cwd if isinstance(cwd, str) and cwd else None


def _evaluate(  # noqa: C901, PLR0911
    harness: str | None,
    raw: object,
) -> tuple[str, Decision, bool]:
    """Return ``(harness, decision, is_post)``; allow when no config exists."""
    name = harness or detect_harness({}, os.environ)
    if isinstance(raw, dict) and harness is None:
        name = detect_harness(raw, os.environ)
    malformed = Decision(allow=False, reason="Malformed hook event")
    if not isinstance(raw, dict):
        return name, malformed, False
    try:
        cwd = _raw_cwd(name, raw)
        if cwd is None:
            return name, malformed, False
        workspace = Path(cwd).resolve()
        for directory in (workspace, *workspace.parents):
            config = directory / ".ml-referee.toml"
            try:
                config.lstat()
            except FileNotFoundError:
                continue
            events = normalize(name, raw)
            if not events:
                return name, malformed, False
            # agy's combined runner sends PostToolUse payloads here; just ack them.
            if name == "agy" and all(
                event.get("hook_event_name") == "PostToolUse" for event in events
            ):
                return name, Decision(allow=True, reason="post"), True
            policy = build_policy(load_config(config), directory)
            for event in events:
                decision = _decide_event(event, policy)
                if not decision.allow:
                    return name, decision, False
            return name, Decision(allow=True, reason="No sealed store access"), False
    except (ValueError, OSError, RuntimeError, ConfigError) as exc:
        reason = f"Guard cannot validate event: {exc}"
        return name, Decision(allow=False, reason=reason), False
    return name, Decision(allow=True, reason="No config"), False


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
        is_post = False
    else:
        harness, decision, is_post = _evaluate(args.harness, raw)
    if is_post:
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
