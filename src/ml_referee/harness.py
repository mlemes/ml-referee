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
    r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$",
)

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

Event = dict[str, object]


def _fail(msg: str) -> NoReturn:
    raise ValueError(msg)


@dataclass(frozen=True)
class _Ctx:
    cwd: str
    post: bool
    exit_code: int | None = None
    failed: bool = False

    def make(self, tool: str, tool_input: dict[str, object]) -> Event:
        return {
            "tool_name": tool,
            "tool_input": tool_input,
            "cwd": self.cwd,
            "hook_event_name": "PostToolUse" if self.post else "PreToolUse",
            "exit_code": self.exit_code,
            "failed": self.failed,
        }


def detect_harness(raw: dict[str, object], environ: Mapping[str, str]) -> str:
    """Guess which harness produced ``raw`` from payload shape and environment."""
    if "toolCall" in raw:
        return "agy"
    if "turn_id" in raw or "permission_mode" in raw:
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
    return [ctx.make("Write", {"file_path": p, "content": patch}) for p in paths]


def _file_tool(kind: str, tin: Mapping[str, object], ctx: _Ctx) -> list[Event]:
    path = _first_str(tin, "file_path", "filePath", "path")
    if path is None:
        _fail(f"{kind} without file path")
    ti: dict[str, object] = {"file_path": path}
    if kind == "Write":
        ti["content"] = _first_str(tin, "content") or ""
    elif kind == "Edit":
        ti["new_string"] = _first_str(tin, "new_string", "newString") or ""
        ti["old_string"] = _first_str(tin, "old_string", "oldString") or ""
    return [ctx.make(kind, ti)]


def _search_tool(kind: str, tin: Mapping[str, object], ctx: _Ctx) -> list[Event]:
    ti: dict[str, object] = {"pattern": _first_str(tin, "pattern") or ""}
    path = _first_str(tin, "path")
    if path is not None:
        ti["path"] = path
    return [ctx.make(kind, ti)]


def _generic(tool: str, tin: Mapping[str, object], ctx: _Ctx) -> list[Event]:
    lowered = tool.lower()
    kind = _TOOL_MAP.get(lowered)
    if lowered == "apply_patch":
        patch = tin.get("command")
        if not isinstance(patch, str):
            _fail("apply_patch without patch text")
        return _patch_events(patch, ctx)
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
    items = [c for c in chunks if isinstance(c, dict)]
    return [
        ctx.make(
            "Edit",
            {
                "file_path": _need(name, args, "TargetFile"),
                "new_string": "\n".join(
                    str(c.get("ReplacementContent", "")) for c in items
                ),
                "old_string": "\n".join(str(c.get("TargetContent", "")) for c in items),
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
    cwd = str(Path.cwd())
    if isinstance(ws, list) and ws and isinstance(ws[0], str):
        cwd = ws[0]
    cwd_arg = args.get("Cwd")
    if isinstance(cwd_arg, str) and cwd_arg:
        cwd = cwd_arg
    ctx = _Ctx(cwd=cwd, post="error" in raw, failed=bool(raw.get("error")))
    events = _agy_dispatch(name, args, ctx)
    if events is None:
        return [ctx.make("Other", {"strings": _strings(args)})]
    return events


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
    post = raw.get("hook_event_name") == "PostToolUse"
    cwd = raw.get("cwd")
    if harness == "devin":
        cwd = os.environ.get("DEVIN_PROJECT_DIR") or str(Path.cwd())
    if not isinstance(cwd, str) or not cwd:
        cwd = str(Path.cwd())
    exit_code: int | None = None
    failed = False
    if harness == "opencode":
        code = raw.get("exit_code")
        if isinstance(code, int) and not isinstance(code, bool):
            exit_code = code
            failed = code != 0
    elif harness == "devin" and post:
        resp = raw.get("tool_response")
        failed = isinstance(resp, dict) and resp.get("success") is False
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
