"""Run harness fixtures through the guard and recorder entry points."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ml_referee.config import load_config, store_dir
from ml_referee.harness import format_decision
from ml_referee.ledger import Ledger

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "harness"
SRC = Path(__file__).resolve().parent.parent / "src"

TOML = """[project]
id = "hhproj"

[data]
train = "train.csv"
label = "y"

[eval]
command = "python eval.py"
metric = "acc"
higher_is_better = true

[trials]
patterns = ["python train.py*"]
"""

# harness -> (pre-tool bash fixture, post-tool ok fixture or None)
BASH = {
    "claude": "claude_bash",
    "codex": "codex_bash",
    "devin": "devin_exec",
    "agy": "agy_bash",
    "opencode": "opencode_bash",
}


def _load(name: str) -> dict[str, object]:
    data: dict[str, object] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


def _retarget(value: object, work: Path) -> object:
    """Replace the fixture project root '/p' with the real workspace."""
    if isinstance(value, str):
        if value == "/p" or value.startswith("/p/"):
            return str(work) + value[2:]
        return value
    if isinstance(value, list):
        return [_retarget(v, work) for v in value]
    if isinstance(value, dict):
        return {k: _retarget(v, work) for k, v in value.items()}
    return value


def _set_command(harness: str, payload: dict[str, object], command: str) -> None:
    if harness == "agy":
        call = payload["toolCall"]
        assert isinstance(call, dict)
        call["args"]["CommandLine"] = command
    else:
        tin = payload["tool_input"]
        assert isinstance(tin, dict)
        tin["command"] = command


def _payload(harness: str, name: str, work: Path, command: str | None) -> str:
    payload = _retarget(copy.deepcopy(_load(name)), work)
    assert isinstance(payload, dict)
    if command is not None:
        _set_command(harness, payload, command)
    return json.dumps(payload)


@pytest.fixture
def proj(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    xdg = tmp_path / "xdg"
    work = tmp_path / "w"
    work.mkdir()
    (work / "train.csv").write_text("a,y\n1,0\n")
    (work / ".ml-referee.toml").write_text(TOML)
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(xdg),
        "PYTHONPATH": str(SRC),
        "DEVIN_PROJECT_DIR": str(work),
    }
    cfg = load_config(work / ".ml-referee.toml")
    old = os.environ.get("XDG_DATA_HOME")
    os.environ["XDG_DATA_HOME"] = str(xdg)
    try:
        store = store_dir(cfg)
    finally:
        if old is None:
            del os.environ["XDG_DATA_HOME"]
        else:
            os.environ["XDG_DATA_HOME"] = old
    store.mkdir(parents=True)
    for name in ("holdout.parquet", "evaluator.py", "manifest.json"):
        (store / name).write_text("x")
    return work, store, env


def _run(
    module: str, harness: str, stdin: str, work: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", f"ml_referee.hooks.{module}", "--harness", harness],
        input=stdin, text=True, cwd=work, env=env, capture_output=True, check=False,
    )


@pytest.mark.parametrize("harness", list(BASH))
def test_guard_denies_store_command(
    harness: str, proj: tuple[Path, Path, dict[str, str]]
) -> None:
    work, store, env = proj
    stdin = _payload(harness, BASH[harness], work, f"cat {store}/holdout.parquet")
    proc = _run("guard", harness, stdin, work, env)
    expected_code = format_decision(harness, False, "r")[2]
    assert proc.returncode == expected_code
    if harness == "claude":
        spec = json.loads(proc.stdout)["hookSpecificOutput"]
        assert spec["permissionDecision"] == "deny"
        assert spec["permissionDecisionReason"]
    elif harness == "agy":
        body = json.loads(proc.stdout)
        assert body["decision"] == "deny"
        assert body["reason"]
    else:
        assert expected_code == 2
        assert proc.stdout == ""
        assert proc.stderr.strip() != ""


@pytest.mark.parametrize("harness", list(BASH))
def test_guard_allows_trial_command(
    harness: str, proj: tuple[Path, Path, dict[str, str]]
) -> None:
    work, _store, env = proj
    stdin = _payload(harness, BASH[harness], work, "python train.py")
    proc = _run("guard", harness, stdin, work, env)
    assert proc.returncode == 0
    if harness == "agy":
        assert json.loads(proc.stdout) == {"decision": "allow"}
    else:
        assert proc.stdout == ""


def test_guard_agy_post_prints_empty_object(
    proj: tuple[Path, Path, dict[str, str]],
) -> None:
    work, _store, env = proj
    stdin = _payload("agy", "agy_post_ok", work, None)
    proc = _run("guard", "agy", stdin, work, env)
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}


def test_guard_codex_apply_patch_on_config_denied(
    proj: tuple[Path, Path, dict[str, str]],
) -> None:
    work, _store, env = proj
    payload = _retarget(_load("codex_apply_patch"), work)
    assert isinstance(payload, dict)
    tin = payload["tool_input"]
    assert isinstance(tin, dict)
    assert isinstance(tin["command"], str)
    tin["command"] = tin["command"].replace(
        "*** Update File: a.py", f"*** Update File: {work}/.ml-referee.toml"
    )
    proc = _run("guard", "codex", json.dumps(payload), work, env)
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.strip() != ""


def test_guard_codex_apply_patch_on_plain_files_allowed(
    proj: tuple[Path, Path, dict[str, str]],
) -> None:
    work, _store, env = proj
    proc = _run("guard", "codex", _payload("codex", "codex_apply_patch", work, None), work, env)
    assert proc.returncode == 0
    assert proc.stdout == ""


def test_guard_unknown_harness_exits_nonzero(
    proj: tuple[Path, Path, dict[str, str]],
) -> None:
    work, _store, env = proj
    stdin = _payload("claude", "claude_bash", work, None)
    proc = _run("guard", "nosuchharness", stdin, work, env)
    assert proc.returncode != 0


def test_recorder_unknown_harness_exits_nonzero(
    proj: tuple[Path, Path, dict[str, str]],
) -> None:
    work, _store, env = proj
    stdin = _payload("codex", "codex_post", work, "python train.py")
    proc = _run("recorder", "nosuchharness", stdin, work, env)
    assert proc.returncode != 0


def test_recorder_codex_post_without_exit_code_is_recorded(
    proj: tuple[Path, Path, dict[str, str]],
) -> None:
    work, store, env = proj
    stdin = _payload("codex", "codex_post", work, "python train.py --lr 1")
    proc = _run("recorder", "codex", stdin, work, env)
    assert proc.returncode == 0
    records = Ledger(store / "ledger.jsonl").records()
    assert len(records) == 1
    assert records[0].payload["command"] == "python train.py --lr 1"
    assert records[0].payload["exit_code"] is None
    assert records[0].payload["failed"] is None


def test_recorder_devin_failure_marks_failed(
    proj: tuple[Path, Path, dict[str, str]],
) -> None:
    work, store, env = proj
    stdin = _payload("devin", "devin_post_fail", work, "python train.py")
    proc = _run("recorder", "devin", stdin, work, env)
    assert proc.returncode == 0
    records = Ledger(store / "ledger.jsonl").records()
    assert len(records) == 1
    assert records[0].payload["failed"] is True


def test_recorder_opencode_exit_code_recorded(
    proj: tuple[Path, Path, dict[str, str]],
) -> None:
    work, store, env = proj
    payload = _retarget(_load("opencode_post_ok"), work)
    assert isinstance(payload, dict)
    tin = payload["tool_input"]
    assert isinstance(tin, dict)
    tin["command"] = "python train.py"
    proc = _run("recorder", "opencode", json.dumps(payload), work, env)
    assert proc.returncode == 0
    records = Ledger(store / "ledger.jsonl").records()
    assert len(records) == 1
    assert records[0].payload["exit_code"] == 0
    assert records[0].payload["failed"] is False


# --- multi-harness hardening: deny paths ---

from ml_referee.hooks.guard import _decide_event  # noqa: E402
from ml_referee.policy import build_policy  # noqa: E402

Proj = tuple[Path, Path, dict[str, str]]


def _raw(tool: str, tin: dict[str, object], work: Path, **extra: object) -> str:
    base: dict[str, object] = {
        "tool_name": tool,
        "tool_input": tin,
        "cwd": str(work),
        "hook_event_name": "PreToolUse",
    }
    base.update(extra)
    return json.dumps(base)


def _denied(proc: subprocess.CompletedProcess[str], harness: str) -> bool:
    if harness == "agy":
        return json.loads(proc.stdout).get("decision") == "deny"
    if harness == "claude":
        return "deny" in proc.stdout
    return proc.returncode == 2


def _agy_payload(work: Path, args: dict[str, object], **extra: object) -> str:
    body: dict[str, object] = {
        "conversationId": "c",
        "workspacePaths": [str(work)],
        "toolCall": {"name": "run_command", "args": args},
    }
    body.update(extra)
    return json.dumps(body)


def test_agy_outside_cwd_arg_still_denied(proj: Proj) -> None:
    work, store, env = proj
    stdin = _agy_payload(work, {"CommandLine": f"cat {store}/holdout.parquet", "Cwd": "/tmp"})
    proc = _run("guard", "agy", stdin, work, env)
    assert json.loads(proc.stdout)["decision"] == "deny"


def test_agy_relative_cwd_still_denied(proj: Proj) -> None:
    work, store, env = proj
    (work / "sub").mkdir()
    stdin = _agy_payload(work, {"CommandLine": f"cat {store}/holdout.parquet", "Cwd": "sub"})
    proc = _run("guard", "agy", stdin, work, env)
    assert json.loads(proc.stdout)["decision"] == "deny"


@pytest.mark.parametrize("error", ["", None, "boom"])
def test_agy_error_key_never_skips_policy(proj: Proj, error: object) -> None:
    work, store, env = proj
    stdin = _agy_payload(work, {"CommandLine": f"cat {store}/holdout.parquet"}, error=error)
    proc = _run("guard", "agy", stdin, work, env)
    assert json.loads(proc.stdout)["decision"] == "deny"


def test_agy_post_with_none_error_runs_policy_and_allows_benign(proj: Proj) -> None:
    work, _store, env = proj
    proc = _run("guard", "agy", _agy_payload(work, {"CommandLine": "true"}, error=None), work, env)
    assert proc.returncode == 0
    assert json.loads(proc.stdout) in ({}, {"decision": "allow"})


@pytest.mark.parametrize(
    ("tool", "tin"),
    [
        ("mcp__fs__write", {"path": ".ml-referee.toml", "text": "x"}),
        ("mcp__fs__read", {"path": "@STORE@/holdout.parquet"}),
        ("mcp__x__do", {"nested": {"deep": [".ml-referee.toml"]}}),
        (
            "patch",
            {"patchText": "*** Begin Patch\n*** Update File: .ml-referee.toml\n@@\n-a\n+b\n"
             "*** End Patch\n"},
        ),
    ],
)
@pytest.mark.parametrize("harness", ["codex", "opencode"])
def test_other_tools_naming_config_or_store_denied(
    proj: Proj, harness: str, tool: str, tin: dict[str, object]
) -> None:
    work, store, env = proj
    tin = json.loads(json.dumps(tin).replace("@STORE@", str(store)))
    proc = _run("guard", harness, _raw(tool, tin, work), work, env)
    assert _denied(proc, harness)


def test_other_tool_without_strings_allowed(proj: Proj) -> None:
    work, _store, env = proj
    proc = _run("guard", "codex", _raw("mcp__x__ping", {}, work), work, env)
    assert proc.returncode == 0


def test_other_tool_cwd_touching_store_is_denied(
    proj: Proj, monkeypatch: pytest.MonkeyPatch
) -> None:
    work, store, env = proj
    monkeypatch.setenv("XDG_DATA_HOME", env["XDG_DATA_HOME"])
    policy = build_policy(load_config(work / ".ml-referee.toml"), work)
    bad = _decide_event(
        {"tool_name": "Other", "tool_input": {"strings": []}, "cwd": str(store)}, policy
    )
    assert bad.allow is False
    ok = _decide_event(
        {"tool_name": "Other", "tool_input": {"strings": []}, "cwd": str(work)}, policy
    )
    assert ok.allow is True


@pytest.mark.parametrize(
    ("tool", "tin"),
    [
        ("read", {"file_path": "a.py", "filePath": "@CFG@"}),
        ("edit", {"file_path": "a.py", "new_string": "", "newString": "@STORE@"}),
        ("write", {"file_path": "a.py", "content": "", "filePath": "@STORE@/x"}),
        ("multiedit", {"file_path": "a.py", "edits": [{"new_string": "@STORE@/x"}]}),
        ("grep", {"pattern": "x", "glob": "@STORE@"}),
        ("grep", {"pattern": "x", "include": "@STORE@"}),
        ("glob", {"pattern": "*.py", "glob": "@STORE@"}),
        ("glob", {"pattern": "*.py", "include": "@STORE@"}),
    ],
)
def test_alias_shadowing_and_extra_keys_denied(
    proj: Proj, tool: str, tin: dict[str, object]
) -> None:
    work, store, env = proj
    text = json.dumps(tin).replace("@STORE@", str(store)).replace("@CFG@", ".ml-referee.toml")
    proc = _run("guard", "codex", _raw(tool, json.loads(text), work), work, env)
    assert proc.returncode == 2


def test_devin_undocumented_key_denied(proj: Proj) -> None:
    work, store, env = proj
    tin = {"file_path": "a.py", "target": f"{store}/holdout.parquet"}
    proc = _run("guard", "devin", _raw("read", tin, work), work, env)
    assert proc.returncode == 2


@pytest.mark.parametrize(
    "header",
    [
        "***Update File:.ml-referee.toml",
        "  *** Update File:   .ml-referee.toml",
        "*** Update File: .ml-referee.toml\r",
    ],
)
def test_apply_patch_header_variants_denied(proj: Proj, header: str) -> None:
    work, _store, env = proj
    patch = f"*** Begin Patch\r\n{header}\r\n@@\r\n-a\r\n+b\r\n*** End Patch\r\n"
    proc = _run("guard", "codex", _raw("apply_patch", {"command": patch}, work), work, env)
    assert proc.returncode == 2


def test_apply_patch_benign_multi_file_allowed(proj: Proj) -> None:
    work, _store, env = proj
    patch = (
        "*** Begin Patch\n*** Update File: a.py\n@@\n-a\n+b\n"
        "*** Add File: sub/b.py\n+x\n*** End Patch\n"
    )
    proc = _run("guard", "codex", _raw("apply_patch", {"command": patch}, work), work, env)
    assert proc.returncode == 0
    assert proc.stdout == ""


def test_claude_with_permission_mode_deny_output_is_byte_identical(proj: Proj) -> None:
    work, store, env = proj
    stdin = _raw(
        "Bash", {"command": f"cat {store}/holdout.parquet"}, work, permission_mode="default"
    )
    proc = _run("guard", "claude", stdin, work, env)
    assert proc.returncode == 0
    parsed = json.loads(proc.stdout)
    assert set(parsed) == {"hookSpecificOutput"}
    spec = parsed["hookSpecificOutput"]
    assert set(spec) == {"hookEventName", "permissionDecision", "permissionDecisionReason"}
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    assert proc.stdout == json.dumps(parsed) + "\n"
    assert proc.stderr == ""


def test_claude_post_with_permission_mode_records_exit_code(proj: Proj) -> None:
    work, store, env = proj
    stdin = _raw(
        "Bash",
        {"command": "python train.py --lr 2"},
        work,
        hook_event_name="PostToolUse",
        permission_mode="default",
        tool_response={"stdout": "", "exit_code": 3},
    )
    proc = _run("recorder", "claude", stdin, work, env)
    assert proc.returncode == 0
    records = Ledger(store / "ledger.jsonl").records()
    assert len(records) == 1
    assert records[0].payload["exit_code"] == 3
    assert records[0].payload["failed"] is True


@pytest.mark.parametrize("command", [[], {"a": 1}])
def test_guard_codex_apply_patch_non_str_command_denied(proj: Proj, command: object) -> None:
    work, _store, env = proj
    proc = _run("guard", "codex", _raw("apply_patch", {"command": command}, work), work, env)
    assert proc.returncode == 2
    assert "Traceback" not in proc.stderr
