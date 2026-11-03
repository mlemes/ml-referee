import io
import json
import statistics
import time
from pathlib import Path

import pytest

from ml_referee.hooks.guard import main
from ml_referee.policy import Policy, build_policy, decide
from ml_referee.config import load_config, store_dir

TOML = """
[project]
id = "proj1"

[data]
train = "train.csv"
label = "y"

[eval]
command = "python eval.py"
metric = "auc"
higher_is_better = true

[trials]
patterns = ["t*.py"]
"""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".ml-referee.toml").write_text(TOML)
    cfg = load_config(ws / ".ml-referee.toml")
    store = store_dir(cfg)
    store.mkdir(parents=True)
    for name in ("holdout.parquet", "evaluator.py", "manifest.json", "ledger.jsonl"):
        (store / name).write_text("x")
    return ws, store


def run_main(
    payload: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    code = main()
    return code, capsys.readouterr().out


def assert_deny(out: str) -> str:
    data = json.loads(out)
    spec = data["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    assert isinstance(spec["permissionDecisionReason"], str)
    assert spec["permissionDecisionReason"]
    return str(spec["permissionDecisionReason"])


def test_main_denies_bash_on_store(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws, store = env
    ev = {
        "tool_name": "Bash",
        "tool_input": {"command": f"cat {store}/holdout.parquet"},
        "cwd": str(ws),
    }
    code, out = run_main(json.dumps(ev), monkeypatch, capsys)
    assert code == 0
    assert_deny(out)


def test_main_denies_read_on_store(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws, store = env
    ev = {
        "tool_name": "Read",
        "tool_input": {"file_path": str(store / "evaluator.py")},
        "cwd": str(ws),
    }
    _, out = run_main(json.dumps(ev), monkeypatch, capsys)
    assert_deny(out)


def test_main_allows_train_command_prints_nothing(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws, _ = env
    ev = {
        "tool_name": "Bash",
        "tool_input": {"command": "python train.py"},
        "cwd": str(ws),
    }
    code, out = run_main(json.dumps(ev), monkeypatch, capsys)
    assert code == 0
    assert out == ""


def test_main_absent_config_allows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    ev = {
        "tool_name": "Bash",
        "tool_input": {"command": "cat /etc/hosts"},
        "cwd": str(other),
    }
    code, out = run_main(json.dumps(ev), monkeypatch, capsys)
    assert code == 0
    assert out == ""


def test_main_invalid_config_denies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws = tmp_path / "bad"
    ws.mkdir()
    (ws / ".ml-referee.toml").write_text("[project]\nid = 3\n")
    ev = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(ws)}
    _, out = run_main(json.dumps(ev), monkeypatch, capsys)
    assert_deny(out)


def test_malformed_json_denies_path_tools(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run_main("{not json", monkeypatch, capsys)
    assert code == 0
    assert_deny(out)


def test_main_denies_config_edit(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws, _ = env
    ev = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(ws / ".ml-referee.toml")},
        "cwd": str(ws),
    }
    _, out = run_main(json.dumps(ev), monkeypatch, capsys)
    assert_deny(out)


def test_guard_median_latency_under_100ms(env: tuple[Path, Path]) -> None:
    ws, store = env
    policy: Policy = build_policy(load_config(ws / ".ml-referee.toml"), ws)
    ev = {
        "tool_name": "Bash",
        "tool_input": {
            "command": f"X={store}; cat $X/holdout.parquet && python train.py"
        },
        "cwd": str(ws),
    }
    times: list[float] = []
    for _ in range(200):
        start = time.perf_counter()
        d = decide(ev, policy)
        times.append(time.perf_counter() - start)
        assert d.allow is False
    assert statistics.median(times) < 0.1


def test_main_denies_store_command_from_workspace_subdirectory(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws, store = env
    sub = ws / "sub" / "deeper"
    sub.mkdir(parents=True)
    ev = {
        "tool_name": "Bash",
        "tool_input": {"command": f"cat {store}/holdout.parquet"},
        "cwd": str(sub),
    }
    code, out = run_main(json.dumps(ev), monkeypatch, capsys)
    assert code == 0
    assert_deny(out)


def test_main_harness_claude_output_matches_default(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws, store = env
    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": f"cat {store}/holdout.parquet"},
        "cwd": str(ws),
    })
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert main() == 0
    default_out = capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert main(["--harness", "claude"]) == 0
    assert capsys.readouterr().out == default_out
    assert_deny(default_out)


def test_main_unknown_harness_is_rejected(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    ws, _store = env
    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(ws)}
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    try:
        code = main(["--harness", "nosuchharness"])
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 1
    assert code != 0


def test_claude_unknown_tool_non_dict_input_allowed_without_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "custom_tool",
            "tool_input": "not-a-dict",
            "cwd": str(bare),
        }
    )
    code, out = run_main(payload, monkeypatch, capsys)
    assert code == 0
    assert out == ""


def test_claude_unknown_tool_nonstandard_key_store_path_denied(
    env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ws, store = env
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__x__y",
            "tool_input": {"weird_key": str(store / "holdout.parquet")},
            "cwd": str(ws),
        }
    )
    _, out = run_main(payload, monkeypatch, capsys)
    assert_deny(out)


def test_invalid_stdin_with_devin_env_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DEVIN_PROJECT_DIR", "/tmp")
    code, _ = run_main("not json", monkeypatch, capsys)
    assert code == 2
