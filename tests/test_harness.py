"""Tests for harness payload normalization and decision formatting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ml_referee.harness import (
    HARNESSES,
    detect_harness,
    format_decision,
    format_post_ack,
    normalize,
)

FIX = Path(__file__).parent / "fixtures" / "harness"


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIX / f"{name}.json").read_text())
    return data


def one(harness: str, name: str) -> dict[str, Any]:
    events = normalize(harness, load(name))
    assert len(events) == 1
    return dict(events[0])


def test_harness_names() -> None:
    assert HARNESSES == ("claude", "codex", "devin", "agy", "opencode")


@pytest.mark.parametrize(
    ("harness", "name", "cmd", "cwd"),
    [
        ("claude", "claude_bash", "ls -la", "/p"),
        ("codex", "codex_bash", "pytest -q", "/p"),
        ("devin", "devin_exec", "ls", "/p"),
        ("agy", "agy_bash", "ls", "/p/sub"),
        ("agy", "agy_bash_nocwd", "ls", "/p"),
        ("opencode", "opencode_bash", "ls", "/p"),
    ],
)
def test_bash_mapping(
    monkeypatch: pytest.MonkeyPatch, harness: str, name: str, cmd: str, cwd: str
) -> None:
    monkeypatch.setenv("DEVIN_PROJECT_DIR", "/p")
    ev = one(harness, name)
    assert ev["tool_name"] == "Bash"
    assert ev["tool_input"]["command"] == cmd
    assert ev["cwd"] == cwd
    assert ev["hook_event_name"] == "PreToolUse"
    assert ev["failed"] is False
    assert ev["exit_code"] is None


@pytest.mark.parametrize(
    ("harness", "name", "tool", "key", "value"),
    [
        ("claude", "claude_read", "Read", "file_path", "/p/a.py"),
        ("claude", "claude_write", "Write", "file_path", "/p/b.py"),
        ("claude", "claude_write", "Write", "content", "print(1)"),
        ("claude", "claude_edit", "Edit", "file_path", "/p/c.py"),
        ("claude", "claude_edit", "Edit", "new_string", "y"),
        ("claude", "claude_grep", "Grep", "pattern", "foo"),
        ("claude", "claude_grep", "Grep", "path", "/p/src"),
        ("claude", "claude_glob", "Glob", "pattern", "**/*.py"),
        ("devin", "devin_read", "Read", "file_path", "/p/a.py"),
        ("devin", "devin_write", "Write", "file_path", "/p/b.py"),
        ("devin", "devin_write", "Write", "content", "x"),
        ("devin", "devin_edit", "Edit", "file_path", "/p/c.py"),
        ("devin", "devin_edit", "Edit", "new_string", "y"),
        ("devin", "devin_grep", "Grep", "pattern", "foo"),
        ("devin", "devin_grep", "Grep", "path", "/p/src"),
        ("devin", "devin_glob", "Glob", "pattern", "*.py"),
        ("agy", "agy_read", "Read", "file_path", "/p/a.py"),
        ("agy", "agy_write", "Write", "file_path", "/p/b.py"),
        ("agy", "agy_write", "Write", "content", "x=1"),
        ("agy", "agy_edit", "Edit", "file_path", "/p/c.py"),
        ("agy", "agy_edit", "Edit", "new_string", "y"),
        ("agy", "agy_grep", "Grep", "path", "/p/src"),
        ("agy", "agy_grep", "Grep", "pattern", "foo"),
        ("agy", "agy_glob", "Glob", "path", "/p"),
        ("agy", "agy_glob", "Glob", "pattern", "*.py"),
        ("opencode", "opencode_read", "Read", "file_path", "/p/a.py"),
        ("opencode", "opencode_write", "Write", "file_path", "/p/b.py"),
        ("opencode", "opencode_write", "Write", "content", "x"),
        ("opencode", "opencode_edit", "Edit", "file_path", "/p/c.py"),
        ("opencode", "opencode_edit", "Edit", "new_string", "y"),
        ("opencode", "opencode_grep", "Grep", "pattern", "foo"),
        ("opencode", "opencode_grep", "Grep", "path", "/p/src"),
        ("opencode", "opencode_glob", "Glob", "pattern", "*.py"),
        ("opencode", "opencode_glob", "Glob", "path", "/p"),
    ],
)
def test_tool_mapping(
    monkeypatch: pytest.MonkeyPatch,
    harness: str,
    name: str,
    tool: str,
    key: str,
    value: str,
) -> None:
    monkeypatch.setenv("DEVIN_PROJECT_DIR", "/p")
    ev = one(harness, name)
    assert ev["tool_name"] == tool
    assert ev["tool_input"][key] == value
    assert ev["cwd"] == "/p"


def test_devin_cwd_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVIN_PROJECT_DIR", "/envdir")
    assert one("devin", "devin_exec")["cwd"] == "/envdir"


def test_devin_cwd_fallback_getcwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DEVIN_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert one("devin", "devin_exec")["cwd"] == str(tmp_path.resolve())


def test_agy_multi_replace_is_edit() -> None:
    ev = one("agy", "agy_multi_edit")
    assert ev["tool_name"] == "Edit"
    assert ev["tool_input"]["file_path"] == "/p/c.py"
    new = str(ev["tool_input"]["new_string"])
    assert "b" in new and "d" in new


def test_codex_apply_patch_one_write_per_header() -> None:
    raw = load("codex_apply_patch")
    events = normalize("codex", raw)
    assert [e["tool_name"] for e in events] == ["Write", "Write"]
    assert [e["tool_input"]["file_path"] for e in events] == ["a.py", "b.py"]
    patch = raw["tool_input"]["command"]
    for e in events:
        assert e["tool_input"]["content"] == patch
        assert e["cwd"] == "/p"
        assert e["hook_event_name"] == "PreToolUse"


def test_codex_apply_patch_move_to_and_delete() -> None:
    raw = load("codex_apply_patch")
    raw["tool_input"]["command"] = (
        "*** Begin Patch\n*** Delete File: old.py\n"
        "*** Update File: a.py\n*** Move to: c.py\n@@\n-x\n+y\n*** End Patch"
    )
    paths = [e["tool_input"]["file_path"] for e in normalize("codex", raw)]
    assert paths == ["old.py", "a.py", "c.py"]


def test_devin_apply_patch_is_write() -> None:
    raw = load("devin_write")
    raw["tool_name"] = "apply_patch"
    raw["tool_input"] = {
        "command": "*** Begin Patch\n*** Add File: n.py\n+z\n*** End Patch"
    }
    events = normalize("devin", raw)
    assert [e["tool_name"] for e in events] == ["Write"]
    assert events[0]["tool_input"]["file_path"] == "n.py"


def test_unknown_tool_is_other_with_strings() -> None:
    ev = one("codex", "codex_mcp")
    assert ev["tool_name"] == "Other"
    assert sorted(ev["tool_input"]["strings"]) == ["hello"]


def test_unknown_tool_keeps_all_string_values() -> None:
    raw = load("claude_bash")
    raw["tool_name"] = "WebFetch"
    raw["tool_input"] = {"url": "http://x", "prompt": "read .ml-referee", "n": 1}
    ev = normalize("claude", raw)[0]
    assert ev["tool_name"] == "Other"
    assert sorted(ev["tool_input"]["strings"]) == ["http://x", "read .ml-referee"]


def test_post_failure_signals() -> None:
    assert one("devin", "devin_post_fail")["failed"] is True
    assert one("devin", "devin_post_fail")["hook_event_name"] == "PostToolUse"
    assert one("devin", "devin_post_ok")["failed"] is False
    assert one("agy", "agy_post_fail")["failed"] is True
    assert one("agy", "agy_post_fail")["hook_event_name"] == "PostToolUse"
    assert one("agy", "agy_post_ok")["failed"] is False
    assert one("agy", "agy_post_ok")["hook_event_name"] == "PostToolUse"
    assert one("opencode", "opencode_post_fail")["failed"] is True
    assert one("opencode", "opencode_post_fail")["exit_code"] == 1
    assert one("opencode", "opencode_post_ok")["failed"] is False
    assert one("opencode", "opencode_post_ok")["exit_code"] == 0


def test_codex_post_has_no_exit_code() -> None:
    ev = one("codex", "codex_post")
    assert ev["hook_event_name"] == "PostToolUse"
    assert ev["exit_code"] is None
    assert ev["failed"] is None


@pytest.mark.parametrize(("code", "failed"), [(0, False), (7, True)])
def test_codex_post_with_exit_code(code: int, failed: bool) -> None:
    raw = load("codex_post")
    raw["tool_response"] = {"stdout": "TRAIN_FAILED\\n", "exit_code": code}
    ev = normalize("codex", raw)[0]
    assert ev["exit_code"] == code
    assert ev["failed"] is failed


def test_codex_post_tool_use_failure_without_exit_code() -> None:
    raw = load("codex_post")
    raw["hook_event_name"] = "PostToolUseFailure"
    ev = normalize("codex", raw)[0]
    assert ev["exit_code"] is None
    assert ev["failed"] is True


@pytest.mark.parametrize(
    "raw",
    [
        ["not", "a", "dict"],
        {"hook_event_name": "PreToolUse", "tool_input": {"command": "ls"}},
        {"hook_event_name": "PreToolUse", "tool_name": "Bash",
         "tool_input": {"command": 5}},
    ],
)
def test_claude_malformed_raises(raw: object) -> None:
    with pytest.raises(ValueError):
        normalize("claude", raw)  # type: ignore[arg-type]


def test_agy_malformed_raises() -> None:
    with pytest.raises(ValueError):
        normalize("agy", {"conversationId": "c"})
    with pytest.raises(ValueError):
        normalize(
            "agy",
            {"toolCall": {"name": "run_command", "args": {"CommandLine": 3}}},
        )


def test_codex_non_dict_raises() -> None:
    with pytest.raises(ValueError):
        normalize("codex", "oops")  # type: ignore[arg-type]


def test_deny_contracts() -> None:
    out, err, code = format_decision("claude", False, "nope")
    assert json.loads(out) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "nope",
        }
    }
    assert (err, code) == ("", 0)
    for h in ("codex", "devin", "opencode"):
        assert format_decision(h, False, "nope") == ("", "nope", 2)
    out, err, code = format_decision("agy", False, "nope")
    assert json.loads(out) == {"decision": "deny", "reason": "nope"}
    assert (err, code) == ("", 0)


def test_allow_contracts() -> None:
    for h in ("claude", "codex", "devin", "opencode"):
        assert format_decision(h, True, "") == ("", "", 0)
    out, err, code = format_decision("agy", True, "")
    assert json.loads(out) == {"decision": "allow"}
    assert (err, code) == ("", 0)


def test_post_ack() -> None:
    assert format_post_ack("agy") == "{}"
    for h in ("claude", "codex", "devin", "opencode"):
        assert format_post_ack(h) == ""


def test_detect_harness() -> None:
    assert detect_harness(load("agy_bash"), {}) == "agy"
    assert detect_harness(load("codex_bash"), {}) == "codex"
    assert detect_harness({"permission_mode": "x"}, {}) == "claude"
    assert detect_harness({"permission_mode": "x", "turn_id": "t"}, {}) == "codex"
    assert detect_harness(load("devin_exec"), {}) == "devin"
    assert detect_harness(load("claude_bash"), {"DEVIN_PROJECT_DIR": "/p"}) == "devin"
    assert detect_harness(load("claude_bash"), {}) == "claude"


def test_apply_patch_without_header_fails_closed() -> None:
    raw = load("codex_apply_patch")
    raw["tool_input"]["command"] = "*** Begin Patch\n@@\n-x\n+y\n*** End Patch"
    with pytest.raises(ValueError):
        normalize("codex", raw)


@pytest.mark.parametrize("name", ["claude_grep", "claude_glob"])
@pytest.mark.parametrize("pattern", [None, 5, ["a"]])
def test_search_bad_pattern_raises(name: str, pattern: object) -> None:
    raw = load(name)
    if pattern is None:
        del raw["tool_input"]["pattern"]
    else:
        raw["tool_input"]["pattern"] = pattern
    with pytest.raises(ValueError):
        normalize("claude", raw)


def test_search_non_str_path_raises() -> None:
    raw = load("claude_grep")
    raw["tool_input"]["path"] = 7
    with pytest.raises(ValueError):
        normalize("claude", raw)


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("claude_write", "content"),
        ("claude_edit", "new_string"),
        ("claude_edit", "old_string"),
        ("claude_read", "path"),
    ],
)
def test_file_tool_non_str_field_raises(name: str, key: str) -> None:
    raw = load(name)
    raw["tool_input"][key] = 3
    with pytest.raises(ValueError):
        normalize("claude", raw)


def _agy_multi(chunks: object) -> dict[str, Any]:
    raw = load("agy_multi_edit")
    raw["toolCall"]["args"]["ReplacementChunks"] = chunks
    return raw


@pytest.mark.parametrize(
    "chunks",
    [
        [],
        ["notadict"],
        [{"ReplacementContent": 1, "TargetContent": "a"}],
        [{"ReplacementContent": "b", "TargetContent": 2}],
        [{"ReplacementContent": "b"}],
    ],
)
def test_agy_multi_edit_bad_chunks_raise(chunks: object) -> None:
    with pytest.raises(ValueError):
        normalize("agy", _agy_multi(chunks))


def test_claude_post_tool_use_failure() -> None:
    raw = load("claude_bash")
    raw["hook_event_name"] = "PostToolUseFailure"
    raw["tool_response"] = {"exit_code": 2}
    ev = normalize("claude", raw)[0]
    assert ev["hook_event_name"] == "PostToolUse"
    assert ev["failed"] is True
    assert ev["exit_code"] == 2


def test_claude_post_tool_use_failure_non_dict_response() -> None:
    raw = load("claude_bash")
    raw["hook_event_name"] = "PostToolUseFailure"
    raw["tool_response"] = "boom"
    ev = normalize("claude", raw)[0]
    assert ev["hook_event_name"] == "PostToolUse"
    assert ev["failed"] is True
    assert ev["exit_code"] is None


@pytest.mark.parametrize(
    ("harness", "name"),
    [
        ("claude", "claude_bash"),
        ("codex", "codex_bash"),
        ("opencode", "opencode_bash"),
    ],
)
@pytest.mark.parametrize("bad", ["missing", "", 5])
def test_bad_cwd_raises(harness: str, name: str, bad: object) -> None:
    raw = load(name)
    if bad == "missing":
        del raw["cwd"]
    else:
        raw["cwd"] = bad
    with pytest.raises(ValueError):
        normalize(harness, raw)


# --- agy cwd, patch headers, aliases, detection (multi-harness hardening) ---


def _agy(name: str, args: dict[str, Any], **extra: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "workspacePaths": ["/p"],
        "toolCall": {"name": name, "args": args},
    }
    raw.update(extra)
    return raw


def test_agy_cwd_arg_outside_workspace_does_not_replace_config_root() -> None:
    ev = normalize("agy", _agy("run_command", {"CommandLine": "ls", "Cwd": "/tmp"}))[0]
    assert ev["cwd"] == "/tmp"
    assert ev["config_root"] == "/p"


def test_agy_relative_cwd_resolves_against_workspace() -> None:
    ev = normalize("agy", _agy("run_command", {"CommandLine": "ls", "Cwd": "sub/x"}))[0]
    assert ev["cwd"] == "/p/sub/x"


def test_detect_harness_permission_mode_without_turn_id_is_claude() -> None:
    raw = {"permission_mode": "default", "tool_name": "Bash", "tool_input": {}}
    assert detect_harness(raw, {}) == "claude"
    assert detect_harness({**raw, "turn_id": "t1"}, {}) == "codex"


@pytest.mark.parametrize("error", [None, 0, False, ["x"]])
def test_agy_error_non_str_is_not_post(error: object) -> None:
    ev = normalize("agy", _agy("run_command", {"CommandLine": "ls"}, error=error))[0]
    assert ev["hook_event_name"] == "PreToolUse"


@pytest.mark.parametrize(("error", "failed"), [("", False), ("boom", True)])
def test_agy_error_str_is_post(error: str, failed: bool) -> None:
    ev = normalize("agy", _agy("run_command", {"CommandLine": "ls"}, error=error))[0]
    assert ev["hook_event_name"] == "PostToolUse"
    assert ev["failed"] is failed


@pytest.mark.parametrize(
    "patch",
    [
        "*** Begin Patch\n***Update File:.ml-referee.toml\n@@\n-a\n+b\n*** End Patch\n",
        "*** Begin Patch\n  *** Update File:   .ml-referee.toml\n@@\n-a\n+b\n*** End Patch\n",
        "*** Begin Patch\r\n*** Update File: .ml-referee.toml\r\n@@\r\n-a\r\n+b\r\n"
        "*** End Patch\r\n",
        "*** Begin Patch\r\n  ***Add File:   .ml-referee.toml  \r\n+x\r\n*** End Patch\r\n",
    ],
)
def test_apply_patch_header_variants_extract_path(patch: str) -> None:
    raw = {
        "tool_name": "apply_patch",
        "tool_input": {"command": patch},
        "cwd": "/p",
        "hook_event_name": "PreToolUse",
    }
    events = normalize("codex", raw)
    assert [e["tool_input"]["file_path"] for e in events] == [".ml-referee.toml"]


def test_apply_patch_multi_file_yields_one_event_per_file() -> None:
    patch = (
        "*** Begin Patch\n*** Update File: a.py\n@@\n-a\n+b\n"
        "*** Add File: b.py\n+x\n*** Delete File: c.py\n*** End Patch\n"
    )
    raw = {
        "tool_name": "apply_patch",
        "tool_input": {"command": patch},
        "cwd": "/p",
        "hook_event_name": "PreToolUse",
    }
    events = normalize("codex", raw)
    assert [e["tool_input"]["file_path"] for e in events] == ["a.py", "b.py", "c.py"]


def test_other_tool_collects_all_strings() -> None:
    raw = {
        "tool_name": "mcp__fs__write",
        "tool_input": {"path": ".ml-referee.toml", "text": "x"},
        "cwd": "/p",
        "hook_event_name": "PreToolUse",
    }
    ev = normalize("codex", raw)[0]
    assert ev["tool_name"] == "Other"
    assert set(ev["tool_input"]["strings"]) == {".ml-referee.toml", "x"}


@pytest.mark.parametrize(
    ("tool", "tin"),
    [
        ("edit", {"file_path": "a.py", "filePath": "/s/x", "new_string": "n"}),
        ("edit", {"file_path": "a.py", "new_string": "", "newString": "/s/x"}),
        ("read", {"file_path": "a.py", "target": "/s/x"}),
        ("grep", {"pattern": "x", "glob": "/s/x"}),
        ("glob", {"pattern": "*.py", "include": "/s/x"}),
        ("multiedit", {"file_path": "a.py", "edits": [{"new_string": "/s/x"}]}),
    ],
)
def test_extra_or_shadowed_keys_are_not_dropped(tool: str, tin: dict[str, Any]) -> None:
    """Every string in the input must survive normalization for the guard to scan."""
    raw = {"tool_name": tool, "tool_input": tin, "cwd": "/p", "hook_event_name": "PreToolUse"}
    events = normalize("codex", raw)
    assert any("/s/x" in json.dumps(e["tool_input"]) for e in events)


@pytest.mark.parametrize("command", [[], {"a": 1}])
def test_apply_patch_non_str_command_raises(command: object) -> None:
    raw = {
        "tool_name": "apply_patch",
        "tool_input": {"command": command},
        "cwd": "/p",
        "hook_event_name": "PreToolUse",
    }
    with pytest.raises(ValueError):
        normalize("codex", raw)
