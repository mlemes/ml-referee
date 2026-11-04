"""PreToolUse guard hook entry point."""

import argparse
import json
import os
import sys
from pathlib import Path

from ml_referee.config import ConfigError, load_config
from ml_referee.harness import (
    HARNESSES,
    _strings,
    detect_harness,
    format_decision,
    format_post_ack,
    normalize,
)
from ml_referee.policy import (
    Decision,
    Policy,
    build_policy,
    decide,
    decide_without_config,
)


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


_ROOT_LIST_KEYS = (
    "workspacePaths",
    "workspace_paths",
    "workspaceRoots",
    "workspace_roots",
    "roots",
)
_ROOT_STR_KEYS = (
    "workspace_root",
    "workspaceRoot",
    "project_dir",
    "projectDir",
    "project_root",
    "projectRoot",
    "workspace",
    "root",
)


def _config_roots(name: str, raw: dict[str, object]) -> list[str]:
    """Return directories to search for ``.ml-referee.toml``, most trusted first.

    Harness-provided roots come before the payload ``cwd``; none of them is a
    tool argument.
    """
    roots: list[str] = []
    if name == "claude":
        roots.append(os.environ.get("CLAUDE_PROJECT_DIR", ""))
    elif name == "devin":
        roots.append(os.environ.get("DEVIN_PROJECT_DIR", ""))
    for key in _ROOT_LIST_KEYS:
        value = raw.get(key)
        if isinstance(value, list):
            roots.extend(item for item in value if isinstance(item, str))
    roots.extend(
        value for key in _ROOT_STR_KEYS if isinstance(value := raw.get(key), str)
    )
    if name in {"agy", "devin"}:
        roots.append(str(Path.cwd()))
    cwd = raw.get("cwd")
    if isinstance(cwd, str):
        roots.append(cwd)
    return [root for root in roots if root]


def _find_config(roots: list[str]) -> tuple[Path, Path] | None:
    """Return ``(config, workspace)`` for the first root that has a config."""
    for root in roots:
        workspace = Path(root).resolve()
        for directory in (workspace, *workspace.parents):
            config = directory / ".ml-referee.toml"
            try:
                config.lstat()
            except FileNotFoundError:
                continue
            return config, directory
    return None


def _finish(name: str, events: list[dict[str, object]], decision: Decision) -> Decision:
    """Turn an allow into the post acknowledgement agy expects."""
    if (
        decision.allow
        and name == "agy"
        and all(event.get("hook_event_name") == "PostToolUse" for event in events)
    ):
        # Policy ran and allowed; agy's post phase expects an empty object.
        return Decision(allow=True, reason="post")
    return decision


def _evaluate(  # noqa: PLR0911, C901
    harness: str | None,
    raw: object,
) -> tuple[str, Decision]:
    """Return ``(harness, decision)``.

    With a config, the full policy applies. Without one, the config-free
    store-root rules apply and everything else is allowed.
    """
    name = harness or detect_harness({}, os.environ)
    if isinstance(raw, dict) and harness is None:
        name = detect_harness(raw, os.environ)
    malformed = Decision(allow=False, reason="Malformed hook event")
    if not isinstance(raw, dict):
        return name, malformed
    try:
        roots = _config_roots(name, raw)
        if not roots:
            return name, malformed
        found = _find_config(roots)
        try:
            events = normalize(name, raw)
        except ValueError:
            if found is not None:
                return name, malformed
            # Unparseable without a config: deny only if it names the store.
            events = [
                {
                    "tool_name": "Other",
                    "tool_input": {"strings": _strings(raw)},
                    "cwd": roots[0],
                },
            ]
        if not events:
            return name, malformed
        if found is None:
            return name, _finish(name, events, decide_without_config(events))
        config, directory = found
        policy = build_policy(load_config(config), directory)
        for event in events:
            decision = _decide_event(event, policy)
            if not decision.allow:
                return name, decision
        return name, _finish(
            name, events, Decision(allow=True, reason="No sealed store access"),
        )
    except (ValueError, OSError, RuntimeError, ConfigError) as exc:
        reason = f"Guard cannot validate event: {exc}"
        return name, Decision(allow=False, reason=reason)


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
