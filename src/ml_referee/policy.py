"""Decide whether a tool call may touch the sealed store."""

import glob
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from ml_referee.config import Config, store_dir, store_root


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


def _resolved_touches(value: str, store: Path, workspace: Path, cwd: Path) -> bool:
    if not value or glob.has_magic(value):
        return False
    try:
        path = _resolve(str(Path(value).expanduser()), cwd)
    except (OSError, RuntimeError, ValueError):
        return "/" in value or "." in value
    return (
        path == store
        or store in path.parents
        or (
            path in store.parents
            and path != workspace
            and path not in workspace.parents
        )
    )


@dataclass(frozen=True)
class Policy:
    """Sealed store and workspace locations."""

    store: Path
    workspace: Path
    cwd: Path | None = None

    def touches_sealed(self, path_or_command: str) -> bool:
        """Return True if the path or shell command may reach the store."""
        try:
            return self._touches(path_or_command)
        except (OSError, RuntimeError, ValueError):
            return True

    def substring_touches(self, text: str) -> bool:
        """Scan literal text without shell parsing or filesystem expansion."""
        workspace = self.workspace.resolve()
        store = self.store.resolve()
        normalized = re.sub(r"[^A-Za-z0-9_./~$-]", "", text)
        for value in (text, normalized):
            if any(
                marker in value
                for marker in (
                    str(store),
                    str(store.parent.parent),
                    ".local/share",
                    "~/.local",
                    "/.local",
                    "XDG_DATA_HOME",
                    *(
                        str(parent)
                        for parent in store.parents
                        if parent != Path("/")
                        and parent != workspace
                        and parent not in workspace.parents
                    ),
                )
            ) or re.search(r"\.local.*share", value):
                return True
        stripped = text
        if store != workspace and workspace not in store.parents:
            spellings = "|".join(
                re.escape(marker)
                for marker in sorted(
                    {str(self.workspace), str(workspace)}, key=len, reverse=True,
                )
            )
            stripped = re.sub(
                rf"(?<![\w./~-])(?:{spellings})(?![\w.-])|\.ml-referee\.toml",
                "",
                text,
            )
        return "ml-referee" in stripped or "ml-referee" in re.sub(
            r"[^A-Za-z0-9_./~$-]", "", stripped,
        )

    def _touches(self, text: str) -> bool:
        if self.substring_touches(text):
            return True
        for candidate in (text, re.sub(r"[^A-Za-z0-9_./~$-]", "", text)):
            try:
                tokens = shlex.split(candidate, posix=True)
            except ValueError:
                tokens = candidate.split()
            for token in tokens:
                pattern = token.split("=", 1)[-1]
                if _resolved_touches(
                    pattern, self.store, self.workspace, self.cwd or self.workspace,
                ):
                    return True
                if glob.has_magic(pattern):
                    value = _expand(pattern, dict(os.environ))
                    for match in glob.iglob(str((self.cwd or self.workspace) / value)):  # noqa: PTH207
                        resolved = Path(match).resolve()
                        if (
                            resolved == self.store
                            or self.store in resolved.parents
                            or (
                                resolved in self.store.parents
                                and resolved != self.workspace
                                and resolved not in self.workspace.parents
                            )
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
        resolved_cwd = _resolve(cwd, policy.workspace)
        if _resolved_touches(
            str(resolved_cwd), policy.store, policy.workspace, policy.workspace,
        ):
            return denied
        active = Policy(policy.store, policy.workspace, resolved_cwd)
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
                try:
                    tokens = shlex.split(value, posix=True)
                except ValueError:
                    tokens = value.split()
                simple = not any(char in value for char in ";&|<>()`\n$")
                config_glob = any(
                    Path(match).resolve()
                    == (policy.workspace / ".ml-referee.toml").resolve()
                    for token in tokens
                    if glob.has_magic(token)
                    for match in glob.iglob(  # noqa: PTH207
                        str(
                            (active.cwd or active.workspace)
                            / _expand(token.split("=", 1)[-1], dict(os.environ)),
                        ),
                    )
                )
                if (".ml-referee.toml" in value or config_glob) and not (
                    simple
                    and tokens
                    and tokens[0]
                    in {"cat", "head", "tail", "less", "ls", "grep", "wc", "stat"}
                ):
                    return denied
                # Only a single CLI invocation gets the trusted-program exception.
                if simple and tokens:
                    if tokens[0] == "ml-referee":
                        value = " ".join(tokens[1:])
                    elif tokens[:3] == ["python", "-m", "ml_referee"]:
                        value = " ".join(tokens[3:])
            if name in {"Write", "Edit"} and key in {"content", "new_string"}:
                if active.substring_touches(value):
                    return denied
                continue
            if active.touches_sealed(value):
                return denied
            if (
                name in {"Grep", "Glob", "Read", "Write", "Edit"}
                and key in {"path", "pattern", "file_path"}
                and _resolved_touches(
                    _expand(value, dict(os.environ)),
                    policy.store,
                    policy.workspace,
                    active.cwd or active.workspace,
                )
            ):
                return denied
            if name in {"Write", "Edit"} and key == "file_path":
                path = _resolve(
                    _expand(value, dict(os.environ)), active.cwd or active.workspace,
                )
                if path == (policy.workspace / ".ml-referee.toml").resolve():
                    return denied
    except (OSError, RuntimeError, ValueError):
        return denied
    return Decision(allow=True, reason="No sealed store access")


_NO_CONFIG_DENIED = Decision(
    allow=False, reason="Access to the ml-referee store root denied",
)
_STORE_TEXT = re.compile(
    r"\.local/share/ml-referee|XDG_DATA_HOME\}?/ml-referee",
)


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _string_values(item)]
    if isinstance(value, (list, tuple)):
        return [s for item in value for s in _string_values(item)]
    return []


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _squash(text: str) -> str:
    """Collapse repeated slashes and ``/./`` segments in literal text."""
    return re.sub(r"/(?:\.?/)+", "/", text)


def _text_names_store(text: str, root: Path, cwd: Path) -> bool:
    """Return True if literal text names the store root (also from an ancestor cwd)."""
    markers = [str(root), str(store_root())]
    if cwd in root.parents:
        # Relative spelling of the store root from an ancestor cwd.
        markers.append(str(root.relative_to(cwd)))
    for value in (text, re.sub(r"[^A-Za-z0-9_./~$-]", "", text)):
        squashed = _squash(value)
        if any(
            _STORE_TEXT.search(item) or any(marker in item for marker in markers)
            for item in (value, squashed)
        ):
            return True
    return False


def _token_in_store(token: str, root: Path, cwd: Path) -> bool:
    """Return True if a path token, or a glob expansion of it, lies in the store."""
    pattern = token.split("=", 1)[-1]
    if not pattern:
        return False
    try:
        expanded = _expand(pattern, dict(os.environ))
        if not glob.has_magic(expanded):
            return _inside(_resolve(expanded, cwd), root)
        for match in glob.iglob(str(cwd / expanded), recursive=True):  # noqa: PTH207
            if _inside(Path(match).resolve(), root):
                return True
    except OSError:
        # A token too long to be a real path cannot name a store entry.
        return False
    return False


_TOKEN_SPLIT = re.compile(r"[\s;&|()<>\"'`]+")


def decide_without_config(events: list[dict[str, object]]) -> Decision:
    """Apply the config-free store-root rules to canonical events.

    Deny when a resolved cwd is inside the store root (or is the XDG data
    directory that holds it), when literal text names the store root, or when
    a resolved path argument lies inside it. Everything else is allowed.
    """
    try:
        root = store_root().resolve()
        for event in events:
            cwd_value = event.get("cwd")
            if not isinstance(cwd_value, str) or not cwd_value:
                return Decision(allow=False, reason="Malformed hook event")
            cwd = _resolve(cwd_value, Path.cwd())
            if _inside(cwd, root) or cwd == root.parent:
                return _NO_CONFIG_DENIED
            for text in _string_values(event.get("tool_input")):
                if _text_names_store(text, root, cwd):
                    return _NO_CONFIG_DENIED
                for candidate in (text, re.sub(r"[^A-Za-z0-9_./~$-]", "", text)):
                    try:
                        tokens = shlex.split(candidate, posix=True)
                    except ValueError:
                        tokens = candidate.split()
                    tokens.extend(_TOKEN_SPLIT.split(candidate))
                    if any(_token_in_store(t, root, cwd) for t in tokens):
                        return _NO_CONFIG_DENIED
    except (OSError, RuntimeError, ValueError):
        return _NO_CONFIG_DENIED
    return Decision(allow=True, reason="No config")
