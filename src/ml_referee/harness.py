"""Harness payload adapters: normalize hook events and format decisions."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

HARNESSES: tuple[str, ...] = ("claude", "codex", "devin", "agy", "opencode")

_PATCH_HEADER = re.compile(
    r"^\s*\*\*\*\s*(?:Add File|Update File|Delete File|Move to):\s*(.+?)\s*$",
)
_PATCH_MARKER = re.compile(r"^\s*\*\*\*\s*(?:Begin Patch|End Patch|End of File)\s*$")
_PATCH_KEYS = ("command", "patch", "patchText")

_TOOL_MAP = {
    "bash": "Bash",
    "exec": "Bash",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "multiedit": "Edit",
    "grep": "Grep",
    "glob": "Glob",
}
_PATCH_TOOLS = ("apply_patch", "patch")

Event = dict[str, object]


def _fail(msg: str) -> NoReturn:
    raise ValueError(msg)


@dataclass(frozen=True)
class _Ctx:
    cwd: str
    post: bool
    exit_code: int | None = None
    failed: bool | None = False
    config_root: str | None = None

    def make(self, tool: str, tool_input: dict[str, object]) -> Event:
        event: Event = {
            "tool_name": tool,
            "tool_input": tool_input,
            "cwd": self.cwd,
            "hook_event_name": "PostToolUse" if self.post else "PreToolUse",
            "exit_code": self.exit_code,
            "failed": self.failed,
        }
        if self.config_root is not None:
            event["config_root"] = self.config_root
        return event


def detect_harness(raw: dict[str, object], environ: Mapping[str, str]) -> str:
    """Guess which harness produced ``raw`` from payload shape and environment."""
    if "toolCall" in raw:
        return "agy"
    if "turn_id" in raw:
        return "codex"
    if "prompt_id" in raw or environ.get("DEVIN_PROJECT_DIR"):
        return "devin"
    if "exit_code" in raw:
        return "opencode"
    return "claude"


def _first_str(src: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        val = src.get(key)
        if isinstance(val, str):
            return val
    return None


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    out: list[str] = []
    if isinstance(value, Mapping):
        for v in value.values():
            out.extend(_strings(v))
    elif isinstance(value, (list, tuple)):
        for v in value:
            out.extend(_strings(v))
    return out


def _patch_events(patch: str, ctx: _Ctx) -> list[Event]:
    paths = []
    for line in patch.splitlines():
        m = _PATCH_HEADER.match(line)
        if m:
            paths.append(m.group(1))
        elif line.lstrip().startswith("***") and not _PATCH_MARKER.match(line):
            _fail(f"apply_patch with unrecognized header: {line.strip()}")
    if not paths:
        _fail("apply_patch without file headers")
    return [ctx.make("Write", {"file_path": p, "content": patch}) for p in paths]


def _pick(src: Mapping[str, object], *keys: str) -> tuple[str, str]:
    """Return the first non-empty str among keys and its key; non-str raises."""
    found, used = "", ""
    for key in keys:
        if key not in src:
            continue
        val = src[key]
        if not isinstance(val, str):
            _fail(f"{key} must be a string")
        if val and not used:
            found, used = val, key
    return found, used


def _with_extras(
    events: list[Event],
    tin: Mapping[str, object],
    used: set[str],
    ctx: _Ctx,
) -> list[Event]:
    """Append an ``Other`` event for every string value the tool mapping dropped."""
    extra: list[str] = []
    for key, value in tin.items():
        if key not in used:
            extra.extend(_strings(value))
    if extra:
        events.append(ctx.make("Other", {"strings": extra}))
    return events


def _file_tool(kind: str, tin: Mapping[str, object], ctx: _Ctx) -> list[Event]:
    aliases = ("file_path", "filePath", "path")
    paths = [
        (key, val) for key in aliases if key in tin for val in [tin[key]]
    ]
    for key, val in paths:
        if not isinstance(val, str):
            _fail(f"{key} must be a string")
    chosen = [(k, str(v)) for k, v in paths if v]
    if not chosen:
        _fail(f"{kind} without file path")
    # Only the first alias counts as "used"; the others are re-scanned as Other.
    used = {chosen[0][0]}
    extras: dict[str, str] = {}
    if kind == "Write":
        extras["content"], key = _pick(tin, "content")
        used.add(key)
    elif kind == "Edit":
        extras["new_string"], key = _pick(tin, "new_string", "newString")
        used.add(key)
        extras["old_string"], key = _pick(tin, "old_string", "oldString")
        used.add(key)
    events = [ctx.make(kind, {"file_path": p, **extras}) for _, p in chosen]
    return _with_extras(events, tin, used, ctx)


def _search_tool(kind: str, tin: Mapping[str, object], ctx: _Ctx) -> list[Event]:
    pattern = tin.get("pattern")
    if not isinstance(pattern, str):
        _fail(f"{kind} pattern must be a string")
    ti: dict[str, object] = {"pattern": pattern}
    used = {"pattern"}
    if "path" in tin:
        path = tin["path"]
        if not isinstance(path, str):
            _fail(f"{kind} path must be a string")
        ti["path"] = path
        used.add("path")
    return _with_extras([ctx.make(kind, ti)], tin, used, ctx)


def _generic(tool: str, tin: Mapping[str, object], ctx: _Ctx) -> list[Event]:
    lowered = tool.lower()
    kind = _TOOL_MAP.get(lowered)
    if lowered in _PATCH_TOOLS:
        values = [tin[k] for k in _PATCH_KEYS if k in tin]
        patches = [v for v in values if isinstance(v, str)]
        if len(patches) != len(values):
            _fail(f"{tool} patch field must be a string")
        texts = set(patches)
        if len(texts) != 1:
            _fail(f"{tool} needs exactly one string patch text")
        return _patch_events(next(iter(texts)), ctx)
    if kind == "Bash":
        cmd = tin.get("command")
        if not isinstance(cmd, str):
            _fail("Bash command must be a string")
        return [ctx.make("Bash", {"command": cmd})]
    if kind in ("Read", "Write", "Edit"):
        return _file_tool(kind, tin, ctx)
    if kind in ("Grep", "Glob"):
        return _search_tool(kind, tin, ctx)
    return [ctx.make("Other", {"strings": _strings(tin)})]


def _need(name: str, args: Mapping[str, object], key: str) -> str:
    val = args.get(key)
    if not isinstance(val, str):
        _fail(f"agy {name}: {key} must be a string")
    return val


def _agy_multi_edit(name: str, args: Mapping[str, object], ctx: _Ctx) -> list[Event]:
    chunks = args.get("ReplacementChunks")
    if not isinstance(chunks, list):
        _fail("agy multi_replace: ReplacementChunks must be a list")
    if not chunks:
        _fail("agy multi_replace: ReplacementChunks is empty")
    items: list[Mapping[str, object]] = []
    for c in chunks:
        if not isinstance(c, dict):
            _fail("agy multi_replace: chunk must be an object")
        for key in ("ReplacementContent", "TargetContent"):
            if not isinstance(c.get(key), str):
                _fail(f"agy multi_replace: chunk {key} must be a string")
        items.append(c)
    return [
        ctx.make(
            "Edit",
            {
                "file_path": _need(name, args, "TargetFile"),
                "new_string": "\n".join(
                    str(c["ReplacementContent"]) for c in items
                ),
                "old_string": "\n".join(str(c["TargetContent"]) for c in items),
            },
        ),
    ]


def _agy_dispatch(
    name: str,
    args: Mapping[str, object],
    ctx: _Ctx,
) -> list[Event] | None:
    def need(key: str) -> str:
        return _need(name, args, key)

    simple: dict[str, Callable[[], dict[str, object]]] = {
        "run_command": lambda: {"command": need("CommandLine")},
        "view_file": lambda: {"file_path": need("AbsolutePath")},
        "write_to_file": lambda: {
            "file_path": need("TargetFile"),
            "content": need("CodeContent"),
        },
        "replace_file_content": lambda: {
            "file_path": need("TargetFile"),
            "new_string": need("ReplacementContent"),
            "old_string": str(args.get("TargetContent", "")),
        },
        "grep_search": lambda: {
            "pattern": need("Query"),
            "path": need("SearchPath"),
        },
        "find_by_name": lambda: {
            "pattern": need("Pattern"),
            "path": need("SearchDirectory"),
        },
    }
    tools = {
        "run_command": "Bash",
        "view_file": "Read",
        "write_to_file": "Write",
        "replace_file_content": "Edit",
        "grep_search": "Grep",
        "find_by_name": "Glob",
    }
    if name in simple:
        return [ctx.make(tools[name], simple[name]())]
    if name == "multi_replace_file_content":
        return _agy_multi_edit(name, args, ctx)
    return None


def _normalize_agy(raw: dict[str, object]) -> list[Event]:
    call = raw.get("toolCall")
    if not isinstance(call, dict):
        _fail("agy payload without toolCall")
    name = call.get("name")
    args = call.get("args")
    if not isinstance(name, str) or not isinstance(args, dict):
        _fail("agy toolCall malformed")
    ws = raw.get("workspacePaths")
    root = str(Path.cwd())
    if isinstance(ws, list) and ws and isinstance(ws[0], str) and ws[0]:
        root = ws[0]
    cwd = root
    cwd_arg = args.get("Cwd")
    if isinstance(cwd_arg, str) and cwd_arg:
        cwd = os.path.normpath(os.path.join(root, cwd_arg))  # noqa: PTH118
    error = raw.get("error")
    ctx = _Ctx(
        cwd=cwd,
        post=isinstance(error, str),
        failed=isinstance(error, str) and bool(error),
        config_root=root,
    )
    events = _agy_dispatch(name, args, ctx)
    if events is None:
        return [ctx.make("Other", {"strings": _strings(args)})]
    return events


def _status(
    harness: str,
    raw: Mapping[str, object],
    *,
    post: bool,
    post_failure: bool,
) -> tuple[int | None, bool | None]:
    exit_code: int | None = None
    failed: bool | None = False
    if harness == "opencode":
        code = raw.get("exit_code")
        if isinstance(code, int) and not isinstance(code, bool):
            exit_code = code
            failed = code != 0
    elif harness == "devin" and post:
        resp = raw.get("tool_response")
        failed = isinstance(resp, dict) and resp.get("success") is False
    elif post or post_failure:
        failed = post_failure
        resp = raw.get("tool_response")
        code = resp.get("exit_code") if isinstance(resp, dict) else None
        if isinstance(code, int) and not isinstance(code, bool):
            exit_code = code
            failed = failed or code != 0
        elif harness == "codex" and post and not post_failure:
            failed = None
    return exit_code, failed


def normalize(harness: str, raw: dict[str, object]) -> list[Event]:
    """Convert a harness payload into canonical events (``ValueError`` if bad)."""
    if not isinstance(raw, dict):
        _fail("payload must be a JSON object")
    if harness == "agy":
        return _normalize_agy(raw)
    if harness not in HARNESSES:
        _fail(f"unknown harness: {harness}")
    tool = raw.get("tool_name")
    tin = raw.get("tool_input")
    if not isinstance(tool, str) or not isinstance(tin, dict):
        _fail("payload needs tool_name and tool_input")
    event = raw.get("hook_event_name")
    post_failure = event == "PostToolUseFailure"
    post = event == "PostToolUse" or post_failure
    cwd = raw.get("cwd")
    if harness == "devin":
        cwd = os.environ.get("DEVIN_PROJECT_DIR") or str(Path.cwd())
    if not isinstance(cwd, str) or not cwd:
        _fail("payload needs a non-empty cwd string")
    exit_code, failed = _status(harness, raw, post=post, post_failure=post_failure)
    ctx = _Ctx(cwd=cwd, post=post, exit_code=exit_code, failed=failed)
    return _generic(tool, tin, ctx)


def format_decision(harness: str, allow: bool, reason: str) -> tuple[str, str, int]:  # noqa: FBT001
    """Return ``(stdout, stderr, exit_code)`` for a pre-tool decision."""
    if harness == "agy":
        body: dict[str, object] = {"decision": "allow"}
        if not allow:
            body = {"decision": "deny", "reason": reason}
        return json.dumps(body), "", 0
    if allow:
        return "", "", 0
    if harness == "claude":
        out = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
        }
        return json.dumps(out), "", 0
    return "", reason, 2


def format_post_ack(harness: str) -> str:
    """Return the stdout acknowledgement a harness expects after PostToolUse."""
    return "{}" if harness == "agy" else ""
