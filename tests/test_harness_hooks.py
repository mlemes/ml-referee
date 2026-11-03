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
    assert records[0].payload["failed"] is False


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
