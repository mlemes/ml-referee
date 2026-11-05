"""Decide whether a tool call may touch the sealed store."""

import glob
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from ml_referee.config import Config, store_dir, store_root
from ml_referee.shellparse import Command, split_commands


def _expanduser(value: str) -> str:
    """Expand ``~`` like the shell: an unknown ``~name`` stays literal.

    ``Path.expanduser`` raises for ``~0.5`` (an approximation in prose), which
    made the guard deny harmless text. ``~`` and ``~/...`` still fail closed.
    """
    try:
        return str(Path(value).expanduser())
    except RuntimeError:
        # ``~+`` and ``~-`` mean $PWD and $OLDPWD in the shell: keep them closed.
        if re.match(r"~[^/+-]", value):
            return value
        raise


def _expand(value: str, variables: dict[str, str]) -> str:
    expanded = re.sub(
        r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))",
        lambda match: variables.get(match[1] or match[2], match[0]),
        value,
    )
    if expanded == "~" or expanded.startswith("~/"):
        return variables.get("HOME", str(Path.home())) + expanded[1:]
    return _expanduser(expanded)


def _resolve(value: str, cwd: Path) -> Path:
    path = Path(value)
    return (cwd / path).resolve()


def _resolved_touches(value: str, store: Path, workspace: Path, cwd: Path) -> bool:
    if not value or glob.has_magic(value):
        return False
    try:
        path = _resolve(_expanduser(value), cwd)
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
                allowed = _cli_verdict(value, policy, active)
                if allowed is False or (
                    allowed is None and _bash_denied(value, policy, active)
                ):
                    return denied
                continue
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



_READ_ONLY = {"cat", "head", "tail", "less", "ls", "grep", "wc", "stat"}


def _config_path(policy: Policy) -> Path:
    return (policy.workspace / ".ml-referee.toml").resolve()


def _config_glob(value: str, policy: Policy, active: Policy) -> bool:
    """Return True if a glob token in ``value`` can match the config file."""
    try:
        tokens = shlex.split(value, posix=True)
    except ValueError:
        tokens = value.split()
    return any(
        Path(match).resolve() == _config_path(policy)
        for token in tokens
        if glob.has_magic(token)
        for match in glob.iglob(  # noqa: PTH207
            str(
                (active.cwd or active.workspace)
                / _expand(token.split("=", 1)[-1], dict(os.environ)),
            ),
            include_hidden=True,
        )
    )


def _bash_denied(value: str, policy: Policy, active: Policy) -> bool:
    """Apply the store rules to one shell text; no plugin CLI exception."""
    try:
        tokens = shlex.split(value, posix=True)
    except ValueError:
        tokens = value.split()
    simple = not any(char in value for char in ";&|<>()`\n$")
    if (
        ".ml-referee.toml" in value or _config_glob(value, policy, active)
    ) and not (simple and tokens and tokens[0] in _READ_ONLY):
        return True
    return active.touches_sealed(value)


# Plugin CLI the agent may run, with the flags each subcommand accepts.
_CLI_FLAGS = {
    "audit": frozenset({"--against", "--sources"}),
    "eval": frozenset({"--predictions", "--predict-cmd"}),
    "verify-ledger": frozenset(),
}
_CLI_HELP = frozenset({*_CLI_FLAGS, "seal", "claim", "install"})
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_UNSAFE_ASSIGN = re.compile(
    r"(?:PATH|PYTHON\w*|LD_\w+|XDG_\w+|HOME|ML_REFEREE\w*|BASH_ENV|ENV|IFS|CDPATH"
    r"|GLOBIGNORE|SHELLOPTS|PS4)=",
)
# Builtins that can redefine commands, options, or what runs next.
_SHELL_STATE = frozenset({
    "shopt", "alias", "unalias", "function", "eval", "source", ".", "builtin",
    "enable", "trap",
})
_DURATION = re.compile(r"\d+(?:\.\d+)?[smhd]?")
_PYTHON = re.compile(r"python(?:\d+(?:\.\d+)*)?")
_PY_FLAG = re.compile(r"-[uBsSEO]+")
_UV_FLAGS = frozenset(
    {"--no-sync", "--frozen", "--locked", "--offline", "-q", "--quiet"},
)
_LEADERS = frozenset({"{", "!", "if", "then", "else", "elif", "do", "while", "until"})
_PLACEHOLDERS = ("{features}", "{out}")
_MAX_CWDS = 8


def _unwrap(words: list[str]) -> list[str] | None:  # noqa: C901, PLR0912
    """Strip leading assignments and the wrappers the agent commonly uses."""
    rest = list(words)
    while rest and rest[0] in _LEADERS:
        rest.pop(0)
    while rest:
        head = rest[0]
        if _ASSIGN.match(head):
            if _UNSAFE_ASSIGN.match(head):
                return None
            rest.pop(0)
        elif head in {"env", "nohup", "time"}:
            rest.pop(0)
            option = {"env": "-i", "time": "-p"}.get(head)
            if option is not None and rest[:1] == [option]:
                rest.pop(0)
        elif head == "timeout":
            rest.pop(0)
            while rest and rest[0].startswith("-"):
                option = rest.pop(0)
                if option in {"-k", "-s", "--kill-after", "--signal"} and rest:
                    rest.pop(0)
            if not rest or not _DURATION.fullmatch(rest.pop(0)):
                return None
        elif head == "uv" and rest[1:2] == ["run"]:
            del rest[:2]
            while rest and rest[0].startswith("-"):
                if rest.pop(0) not in _UV_FLAGS:
                    return None
        else:
            break
    return rest


def _cli_argv(rest: list[str]) -> list[str] | None:
    """Return the arguments after the plugin CLI program, or None."""
    if rest[:1] == ["ml-referee"]:
        return rest[1:]
    if rest and _PYTHON.fullmatch(rest[0]):
        index = 1
        while index < len(rest) and _PY_FLAG.fullmatch(rest[index]):
            index += 1
        if rest[index : index + 1] == ["-m"] and rest[index + 1 : index + 2] in (
            ["ml_referee"],
            ["ml_referee.cli"],
        ):
            return rest[index + 2 :]
    return None


def _value_denied(value: str, policy: Policy, cwds: list[Path]) -> bool:
    """Return True if a CLI argument or redirect target may reach the store."""
    if any(char in value for char in "$`{}"):
        return True
    for cwd in cwds:
        active = Policy(policy.store, policy.workspace, cwd)
        if (
            ".ml-referee.toml" in value
            or _config_glob(value, policy, active)
            or active.touches_sealed(value)
        ):
            return True
    return False


def _argv_allowed(  # noqa: C901
    argv: list[str], policy: Policy, cwds: list[Path],
) -> bool:
    """Check the subcommand, its flags, and every path or command it carries."""
    if not argv:
        return False
    if argv in (["-h"], ["--help"]) or (
        argv[0] in _CLI_HELP and argv[1:] in (["-h"], ["--help"])
    ):
        return True
    allowed = _CLI_FLAGS.get(argv[0])
    if allowed is None:
        return False
    flag = ""
    for word in argv[1:]:
        values = [word]
        if word.startswith("-"):
            flag, _, value = word.partition("=")
            if flag not in allowed:
                return False
            values = [value] if value else []
        for value in values:
            text = value
            if flag == "--predict-cmd":
                for placeholder in _PLACEHOLDERS:
                    text = text.replace(placeholder, "")
            if any(char in text for char in "{}") or _value_denied(text, policy, cwds):
                return False
    return True


def _redirects_allowed(command: Command, policy: Policy, cwds: list[Path]) -> bool:
    for operator, target in command.redirects:
        if operator.endswith("&") and (target.isdigit() or target == "-"):
            continue  # file descriptor duplication, not a path
        if _value_denied(target, policy, cwds):
            return False
    return True


def _next_cwds(words: list[str], cwds: list[Path]) -> list[Path] | None:
    """Add the directories a ``cd`` can reach; None when they are not static."""
    args = words[1:]
    if words[0] in {"pushd", "popd"} or len(args) > 1:
        return None
    target = args[0] if args else "~"
    if not target or target.startswith("-") or any(c in target for c in "$`{}*?["):
        return None
    try:
        reached = [
            _resolve(_expand(target, dict(os.environ)), cwd) for cwd in cwds
        ]
    except (OSError, RuntimeError, ValueError):
        return None
    merged = [*cwds, *(path for path in reached if path not in cwds)]
    return merged if len(merged) <= _MAX_CWDS else None


def _cli_verdict(  # noqa: C901, PLR0911, PLR0912
    value: str, policy: Policy, active: Policy,
) -> bool | None:
    """Judge a command that runs the plugin CLI.

    Return None when it holds no plugin CLI segment (the store rules apply to
    the whole text), True to allow, False to deny. Every segment other than the
    CLI goes through the store rules; the CLI's arguments and every redirection
    target must stay outside the store and away from the config.
    """
    commands = split_commands(value)
    if commands is None or "CDPATH" in value:
        return None
    parsed = []
    for command in commands:
        if any(_UNSAFE_ASSIGN.match(word) for word in command.words):
            return False
        rest = _unwrap(list(command.words))
        argv = _cli_argv(rest) if rest is not None else None
        probe = rest in (["which", "ml-referee"], ["command", "-v", "ml-referee"])
        if (rest or [""])[0] in _SHELL_STATE:
            return False
        if any(word in {"cd", "pushd", "popd"} for word in command.words) and (
            not rest or rest[0] not in {"cd", "pushd", "popd"}
        ):
            return False  # a ``cd`` we cannot place in the command order
        parsed.append((command, argv, probe, rest))
    if not any(argv is not None or probe for _, argv, probe, _ in parsed):
        return None
    cwds = [active.cwd or active.workspace]
    for command, argv, probe, rest in parsed:
        if not _redirects_allowed(command, policy, cwds):
            return False
        if argv is not None:
            if (
                ".ml-referee.toml" in command.text
                or _config_glob(command.text, policy, active)
            ):
                return False
            lead = command.words[: len(command.words) - len(argv)]
            if any(
                word not in {"ml-referee", *_LEADERS}
                and _value_denied(word, policy, cwds)
                for word in lead
            ):
                return False
            if not _argv_allowed(argv, policy, cwds):
                return False
        elif not probe:
            for cwd in cwds:
                at_cwd = Policy(policy.store, policy.workspace, cwd)
                # No read-only exemption for the config here: it applies to a
                # command typed alone, not to a segment of a longer line.
                if (
                    ".ml-referee.toml" in command.text
                    or _config_glob(command.text, policy, at_cwd)
                    or at_cwd.touches_sealed(command.text)
                ):
                    return False
            if rest and rest[0] in {"cd", "pushd", "popd"}:
                reached = _next_cwds(rest, cwds)
                if reached is None:
                    return False
                cwds = reached
    return True

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
    for pattern in {token, token.split("=", 1)[-1]}:
        if pattern and _pattern_in_store(pattern, root, cwd):
            return True
    return False


def _pattern_in_store(pattern: str, root: Path, cwd: Path) -> bool:
    try:
        expanded = _expand(pattern, dict(os.environ))
        if not glob.has_magic(expanded):
            return _inside(_resolve(expanded, cwd), root)
        # Walk the filesystem only when the static prefix can reach the store.
        parts = expanded.split("/")
        static = []
        for part in parts:
            if glob.has_magic(part):
                break
            static.append(part)
        prefix = _resolve("/".join(static) or ".", cwd)
        if _inside(prefix, root):
            return True
        if prefix != root and prefix not in root.parents:
            return False
        return any(
            _inside(Path(match).resolve(), root)
            for match in glob.iglob(str(cwd / expanded))  # noqa: PTH207
        )
    except OSError:
        # A token too long to be a real path cannot name a store entry.
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
