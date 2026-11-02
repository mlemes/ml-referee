import hashlib
import io
import json
import os
from pathlib import Path

import pytest

from ml_referee.config import Config, load_config, store_dir
from ml_referee.hooks.recorder import main, record
from ml_referee.ledger import Ledger

TOML = """
[project]
id = "recproj"

[data]
train = "train.csv"
label = "y"

[eval]
command = "python eval.py"
metric = "auc"
higher_is_better = true

[trials]
patterns = ["python train.py"]
"""


def _setup(tmp_path: Path) -> tuple[Config, Ledger]:
    (tmp_path / "train.csv").write_text("a,y\n1,0\n")
    path = tmp_path / ".ml-referee.toml"
    path.write_text(TOML)
    cfg = load_config(path)
    return cfg, Ledger(tmp_path / "ledger.jsonl")


def _event(
    command: str,
    exit_code: int | None = 0,
    hook: str = "PostToolUse",
    tool: str = "Bash",
) -> dict[str, object]:
    response: dict[str, object] = {"stdout": "ok"}
    if exit_code is not None:
        response["exit_code"] = exit_code
    return {
        "hook_event_name": hook,
        "tool_name": tool,
        "tool_input": {"command": command},
        "tool_response": response,
        "cwd": "/tmp",
    }


def test_matching_command_appends_trial(tmp_path: Path) -> None:
    cfg, ledger = _setup(tmp_path)
    rec = record(_event("python train.py --lr 3e-4"), cfg, ledger)
    assert rec is not None
    assert rec.kind == "trial"
    assert rec.payload["command"] == "python train.py --lr 3e-4"
    assert rec.payload["exit_code"] == 0
    assert rec.payload["failed"] is False
    assert len(ledger.records()) == 1


def test_nonmatching_command_ignored(tmp_path: Path) -> None:
    cfg, ledger = _setup(tmp_path)
    assert record(_event("ls"), cfg, ledger) is None
    assert record(_event("python train.pyx"), cfg, ledger) is None
    assert ledger.records() == []


def test_failed_command_still_recorded(tmp_path: Path) -> None:
    cfg, ledger = _setup(tmp_path)
    rec = record(_event("python train.py", exit_code=1), cfg, ledger)
    assert rec is not None
    assert rec.payload["exit_code"] == 1
    assert rec.payload["failed"] is True


def test_post_tool_use_failure_event_marked_failed(tmp_path: Path) -> None:
    cfg, ledger = _setup(tmp_path)
    rec = record(
        _event("python train.py", exit_code=None, hook="PostToolUseFailure"),
        cfg,
        ledger,
    )
    assert rec is not None
    assert rec.payload["exit_code"] is None
    assert rec.payload["failed"] is True


def test_missing_exit_code_not_failed(tmp_path: Path) -> None:
    cfg, ledger = _setup(tmp_path)
    rec = record(_event("python train.py", exit_code=None), cfg, ledger)
    assert rec is not None
    assert rec.payload["exit_code"] is None
    assert rec.payload["failed"] is False


def test_non_bash_tool_ignored(tmp_path: Path) -> None:
    cfg, ledger = _setup(tmp_path)
    assert record(_event("python train.py", tool="Edit"), cfg, ledger) is None
    assert ledger.records() == []


def test_train_sha256_present(tmp_path: Path) -> None:
    cfg, ledger = _setup(tmp_path)
    rec = record(_event("python train.py"), cfg, ledger)
    assert rec is not None
    expected = hashlib.sha256((tmp_path / "train.csv").read_bytes()).hexdigest()
    assert rec.payload["train_sha256"] == expected


def test_train_sha256_none_when_file_missing(tmp_path: Path) -> None:
    cfg, ledger = _setup(tmp_path)
    cfg.train_path.unlink()
    rec = record(_event("python train.py"), cfg, ledger)
    assert rec is not None
    assert rec.payload["train_sha256"] is None


def test_main_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, _ = _setup(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    os.makedirs(store_dir(cfg))
    sub = tmp_path / "sub" / "dir"
    sub.mkdir(parents=True)
    event = _event("python train.py --x 1")
    event["cwd"] = str(sub)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert main() == 0
    records = Ledger(store_dir(cfg) / "ledger.jsonl").records()
    assert len(records) == 1
    assert records[0].kind == "trial"
    assert records[0].payload["command"] == "python train.py --x 1"


def test_main_without_config_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    work = tmp_path / "work"
    work.mkdir()
    event = _event("python train.py")
    event["cwd"] = str(work)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert main() == 0
    assert not xdg.exists()


@pytest.mark.parametrize(
    "command",
    [
        "cd x && python train.py",
        "cd x; python train.py --lr 1",
        "false || python train.py",
        "make data | python train.py",
        "FOO=1 BAR=2 python train.py",
        "nohup python train.py",
        "env FOO=1 python train.py",
        "time python train.py",
        "nice -n 5 python train.py",
        "bash -c 'python train.py'",
        "python   train.py",
        "echo start\npython train.py --lr 1",
    ],
)
def test_wrapped_or_compound_command_recorded(tmp_path: Path, command: str) -> None:
    cfg, ledger = _setup(tmp_path)
    rec = record(_event(command), cfg, ledger)
    assert rec is not None
    assert rec.kind == "trial"
    assert rec.payload["command"] == command
    assert len(ledger.records()) == 1


@pytest.mark.parametrize(
    "command", ["echo python train.py", "cat train.py", "python other.py"]
)
def test_lookalike_command_not_recorded(tmp_path: Path, command: str) -> None:
    cfg, ledger = _setup(tmp_path)
    assert record(_event(command), cfg, ledger) is None
    assert ledger.records() == []


def _run_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdin_text: str
) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    return main()


def test_main_malformed_json_returns_zero_with_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run_main(tmp_path, monkeypatch, "{not json") == 0
    assert capsys.readouterr().err.strip() != ""


def test_main_ledger_append_failure_warns_on_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg, _ = _setup(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    os.makedirs(store_dir(cfg))
    (store_dir(cfg) / "ledger.jsonl").write_text("corrupted\n")
    event = _event("python train.py")
    event["cwd"] = str(tmp_path)
    assert _run_main(tmp_path, monkeypatch, json.dumps(event)) == 0
    assert "ml-referee" in capsys.readouterr().err


def test_main_without_config_stderr_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    work = tmp_path / "work"
    work.mkdir()
    event = _event("python train.py")
    event["cwd"] = str(work)
    assert _run_main(tmp_path, monkeypatch, json.dumps(event)) == 0
    assert capsys.readouterr().err == ""
