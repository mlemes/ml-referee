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

    def _touches(self, text: str) -> bool:  # noqa: C901, PLR0911
        # Only a single CLI invocation gets the trusted-program exception.
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
        if (
            tokens
            and Path(tokens[0]).name == "ml-referee"
            and not any(char in text for char in ";&|<>()`\n$")
        ):
            return False
        if str(self.store) in text or (
            "ml-referee/" in text
            and (".local/share" in text or "XDG_DATA_HOME" in text)
        ):
            return True
        variables = dict(os.environ)
        raw_path = _resolve(_expand(text, variables), self.workspace)
        if raw_path == self.store or self.store in raw_path.parents:
            return True
        cwd = self.workspace
        for index, token in enumerate(tokens):
            value = _expand(token, variables)
            assignment = re.fullmatch(r"([A-Za-z_]\w*)=(.*)", value)
            if assignment:
                variables[assignment[1]] = assignment[2]
                value = assignment[2]
            if str(self.store) in value:
                return True
            if "$" in value or "`" in value:
                return True
            path = _resolve(value, cwd)
            if path == self.store or self.store in path.parents:
                return True
            if glob.has_magic(value):
                for match in glob.iglob(str(cwd / value)):  # noqa: PTH207
                    resolved = Path(match).resolve()
                    if (
                        resolved == self.store
                        or self.store in resolved.parents
                        or resolved in self.store.parents
                    ):
                        return True
            if index and tokens[index - 1] == "cd":
                cwd = path
        return False


@dataclass(frozen=True)
class Decision:
    """Allow or deny verdict with a reason."""

    allow: bool
    reason: str


def build_policy(cfg: Config, workspace: Path) -> Policy:
    """Build a policy for ``cfg`` and ``workspace``."""
    return Policy(store_dir(cfg).resolve(), workspace.resolve())


def decide(event: dict[str, object], policy: Policy) -> Decision:  # noqa: C901, PLR0911
    """Decide on one PreToolUse event."""
    name = event.get("tool_name")
    inputs = event.get("tool_input")
    if not isinstance(name, str) or not isinstance(inputs, dict):
        return Decision(allow=False, reason="Malformed tool event")
    cwd = event.get("cwd", str(policy.workspace))
    if not isinstance(cwd, str):
        return Decision(allow=False, reason="Malformed working directory")
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
            return Decision(allow=False, reason="Missing tool path or command")
        for key in ("command", "file_path", "path", "pattern"):
            value = inputs.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                return Decision(allow=False, reason="Malformed tool path or command")
            if active.touches_sealed(value):
                return Decision(
                    allow=False, reason=f"Access to sealed store {policy.store} denied",
                )
            if name in {"Write", "Edit"} and key == "file_path":
                path = _resolve(_expand(value, dict(os.environ)), active.workspace)
                if path == (policy.workspace / ".ml-referee.toml").resolve():
                    return Decision(
                        allow=False, reason="Workspace configuration is protected",
                    )
    except (OSError, RuntimeError, ValueError):
        return Decision(allow=False, reason="Cannot safely resolve tool paths")
    return Decision(allow=True, reason="No sealed store access")
