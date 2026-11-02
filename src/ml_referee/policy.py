"""Decide whether a tool call may touch the sealed store."""

import glob
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from ml_referee.config import Config, store_dir


def _expand(value: str, variables: dict[str, str]) -> str:
    expanded = re.sub(
        r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))",
        lambda match: variables.get(match[1] or match[2], match[0]),
        value,
    )
    if expanded == "~" or expanded.startswith("~/"):
        return variables.get("HOME", str(Path.home())) + expanded[1:]
    return str(Path(expanded).expanduser())


def _resolve(value: str, cwd: Path) -> Path:
    path = Path(value)
    return (cwd / path).resolve()


@dataclass(frozen=True)
class Policy:
    """Sealed store and workspace locations."""

    store: Path
    workspace: Path

    def touches_sealed(self, path_or_command: str) -> bool:
        """Return True if the path or shell command may reach the store."""
        try:
            return self._touches(path_or_command)
        except (OSError, RuntimeError, ValueError):
            return True

    def _touches(self, text: str) -> bool:
        normalized = re.sub(r"[^A-Za-z0-9_./~$-]", "", text)
        for value in (text, normalized):
            if any(
                marker in value
                for marker in (
                    str(self.store),
                    str(self.store.parent.parent),
                    "ml-referee",
                    ".local/share",
                    "~/.local",
                    "/.local",
                    "XDG_DATA_HOME",
                    *(
                        str(parent)
                        for parent in self.store.parents
                        if parent != Path("/")
                        and parent != self.workspace
                        and parent not in self.workspace.parents
                    ),
                )
            ) or re.search(r"\.local.*share", value):
                return True
        for token in shlex.split(text, posix=True):
            pattern = token.split("=", 1)[-1]
            if glob.has_magic(pattern):
                value = _expand(pattern, dict(os.environ))
                for match in glob.iglob(str(self.workspace / value)):  # noqa: PTH207
                    resolved = Path(match).resolve()
                    if (
                        resolved == self.store
                        or self.store in resolved.parents
                        or resolved in self.store.parents
                    ):
                        return True
        return False


@dataclass(frozen=True)
class Decision:
    """Allow or deny verdict with a reason."""

    allow: bool
    reason: str


def build_policy(cfg: Config, workspace: Path) -> Policy:
    """Build a policy for ``cfg`` and ``workspace``."""
    return Policy(store_dir(cfg).resolve(), workspace.resolve())


def decide(event: dict[str, object], policy: Policy) -> Decision:  # noqa: C901, PLR0911, PLR0912
    """Decide on one PreToolUse event."""
    denied = Decision(
        allow=False, reason=f"Access to sealed store {policy.store} denied",
    )
    name = event.get("tool_name")
    inputs = event.get("tool_input")
    if not isinstance(name, str) or not isinstance(inputs, dict):
        return denied
    cwd = event.get("cwd", str(policy.workspace))
    if not isinstance(cwd, str):
        return denied
    try:
        active = Policy(policy.store, _resolve(cwd, policy.workspace))
        required = {
            "Bash": "command",
            "Read": "file_path",
            "Write": "file_path",
            "Edit": "file_path",
            "Glob": "pattern",
        }
        if name in required and not isinstance(inputs.get(required[name]), str):
            return denied
        keys = ["command", "file_path", "path", "pattern"]
        if name in {"Write", "Edit"}:
            keys.extend(("content", "new_string"))
        for key in keys:
            value = inputs.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                return denied
            if name == "Bash" and key == "command":
                tokens = shlex.split(value, posix=True)
                simple = not any(char in value for char in ";&|<>()`\n$")
                config_glob = any(
                    Path(match).resolve()
                    == (policy.workspace / ".ml-referee.toml").resolve()
                    for token in tokens
                    if glob.has_magic(token)
                    for match in glob.iglob(  # noqa: PTH207
                        str(
                            active.workspace
                            / _expand(token.split("=", 1)[-1], dict(os.environ)),
                        ),
                    )
                )
                if ".ml-referee.toml" in value or config_glob:
                    if not (
                        simple
                        and tokens
                        and tokens[0]
                        in {"cat", "head", "tail", "less", "ls", "grep", "wc", "stat"}
                    ):
                        return denied
                    value = value.replace(".ml-referee.toml", "")
                # Only a single CLI invocation gets the trusted-program exception.
                if simple and tokens:
                    if tokens[0] == "ml-referee":
                        value = " ".join(tokens[1:])
                    elif tokens[:3] == ["python", "-m", "ml_referee"]:
                        value = " ".join(tokens[3:])
            if active.touches_sealed(value):
                return denied
            if name in {"Grep", "Glob", "Read"} and key in {
                "path",
                "pattern",
                "file_path",
            }:
                path = _resolve(_expand(value, dict(os.environ)), active.workspace)
                if path in policy.store.parents and not (
                    path == policy.workspace or path in policy.workspace.parents
                ):
                    return denied
            if name in {"Write", "Edit"} and key == "file_path":
                path = _resolve(_expand(value, dict(os.environ)), active.workspace)
                if path == (policy.workspace / ".ml-referee.toml").resolve():
                    return denied
    except (OSError, RuntimeError, ValueError):
        return denied
    return Decision(allow=True, reason="No sealed store access")
