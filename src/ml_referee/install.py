"""Install ml-referee hooks into agent harness configuration files."""

import json
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ml_referee
from ml_referee.config import ConfigError, load_config
from ml_referee.harness import HARNESSES

OPENCODE_PLUGIN_SRC: Path = (
    Path(__file__).resolve().parent / "data" / "opencode" / "ml-referee.js"
)

_MARKER = "ml_referee.hooks."
_TIMEOUT = 30
_POST_MATCHER = {"codex": "^Bash$", "devin": "^exec$", "agy": "run_command"}
_TARGETS = {
    "codex": Path(".codex") / "hooks.json",
    "devin": Path(".devin") / "hooks.v1.json",
    "agy": Path(".agents") / "hooks.json",
}


@dataclass(frozen=True)
class Change:
    """One file change made (or planned) by the installer."""

    path: Path
    action: str


def _entry(harness: str, module: str, matcher: str, python: str) -> dict[str, Any]:
    src = shlex.quote(str(Path(ml_referee.__file__).resolve().parent.parent))
    command = (
        f"env PYTHONPATH={src} {shlex.quote(python)} "
        f"-m ml_referee.hooks.{module} --harness {harness}"
    )
    return {
        "matcher": matcher,
        "hooks": [{"type": "command", "command": command, "timeout": _TIMEOUT}],
    }


def _is_ours(hook: Any) -> bool:  # noqa: ANN401
    return isinstance(hook, dict) and _MARKER in str(hook.get("command", ""))


def _strip_ours(entry: Any) -> list[Any]:  # noqa: ANN401
    """Drop our hooks from an entry, keeping the entry if user hooks remain."""
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    if not isinstance(hooks, list):
        return [entry]
    kept = [h for h in hooks if not _is_ours(h)]
    if len(kept) == len(hooks):
        return [entry]
    return [{**entry, "hooks": kept}] if kept else []


def _merge_events(events: dict[str, Any], harness: str, python: str) -> None:
    wanted = {
        "PreToolUse": _entry(harness, "guard", "", python),
        "PostToolUse": _entry(harness, "recorder", _POST_MATCHER[harness], python),
    }
    for name, entry in wanted.items():
        current = events.get(name, [])
        if not isinstance(current, list):
            msg = f"Existing {name} hooks must be a JSON list"
            raise ValueError(msg)  # noqa: TRY004
        events[name] = [k for e in current for k in _strip_ours(e)] + [entry]


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    section = data.setdefault(key, {})
    if not isinstance(section, dict):
        msg = f"Existing '{key}' must be a JSON object"
        raise ValueError(msg)  # noqa: TRY004
    return section


def _install_json(harness: str, root: Path, dry_run: bool, python: str) -> Change:  # noqa: FBT001
    target = root / _TARGETS[harness]
    exists = target.exists()
    data: dict[str, Any] = {}
    if exists:
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
        except ValueError as exc:
            msg = f"{target} is not valid JSON: {exc}"
            raise ValueError(msg) from exc
        if not isinstance(loaded, dict):
            msg = f"{target} must contain a JSON object"
            raise ValueError(msg)
        data = loaded
    before = json.dumps(data, sort_keys=True)
    if harness == "codex":
        events = _section(data, "hooks")
    elif harness == "agy":
        events = _section(data, "ml-referee")
        events["enabled"] = True
    else:
        events = data
    _merge_events(events, harness, python)
    if exists and json.dumps(data, sort_keys=True) == before:
        return Change(target, "unchanged")
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return Change(target, "merge" if exists else "create")


def _install_opencode(root: Path, dry_run: bool, python: str) -> Change:  # noqa: FBT001
    src = str(Path(ml_referee.__file__).resolve().parent.parent)
    content = (
        OPENCODE_PLUGIN_SRC.read_text(encoding="utf-8")
        .replace(
            "null // ml-referee:pythonpath",
            f"{json.dumps(src)} // ml-referee:pythonpath",
        )
        .replace(
            "null // ml-referee:python\n",
            f"{json.dumps(python)} // ml-referee:python\n",
        )
    )
    target = root / ".opencode" / "plugins" / "ml-referee.js"
    exists = target.exists()
    if exists and target.read_text(encoding="utf-8") == content:
        return Change(target, "unchanged")
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return Change(target, "merge" if exists else "create")


def _budget(root: Path) -> int | None:
    """Return the holdout budget from ``root``'s config, or None when unknown."""
    config = root / ".ml-referee.toml"
    if not config.is_file():
        return None
    try:
        return load_config(config).holdout_budget
    except ConfigError:
        return None


def install(
    harness: str,
    root: Path,
    *,
    dry_run: bool = False,
    python: str | None = None,
) -> list[Change]:
    """Install hooks for one harness under root and report the changes."""
    if harness not in HARNESSES:
        msg = f"Unknown harness: {harness}"
        raise ValueError(msg)
    if harness == "claude":
        sys.stdout.write("Claude Code: run `claude --plugin-dir <ml-referee repo>`\n")
        return []
    from ml_referee.agents_md import apply_block  # noqa: PLC0415 (avoids import cycle)

    if harness == "opencode":
        changes = [_install_opencode(root, dry_run, python or sys.executable)]
    else:
        changes = [_install_json(harness, root, dry_run, python or sys.executable)]
    changes.append(
        apply_block(root / "AGENTS.md", dry_run=dry_run, budget=_budget(root)),
    )
    return changes
