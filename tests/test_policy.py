from pathlib import Path

import pytest

from ml_referee.config import load_config, store_dir
from ml_referee.policy import Policy, build_policy, decide

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
def policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Policy:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".ml-referee.toml").write_text(TOML)
    (ws / "train.py").write_text("print(1)\n")
    cfg = load_config(ws / ".ml-referee.toml")
    store = store_dir(cfg)
    store.mkdir(parents=True)
    for name in ("holdout.parquet", "evaluator.py", "manifest.json", "ledger.jsonl"):
        (store / name).write_text("x")
    return build_policy(cfg, ws)


def bash(cmd: str, policy: Policy) -> dict[str, object]:
    return {
        "tool_name": "Bash",
        "tool_input": {"command": cmd},
        "cwd": str(policy.workspace),
    }


def tool(name: str, key: str, value: str, policy: Policy) -> dict[str, object]:
    return {
        "tool_name": name,
        "tool_input": {key: value},
        "cwd": str(policy.workspace),
    }


def test_build_policy_paths(policy: Policy, tmp_path: Path) -> None:
    assert policy.store == tmp_path / "home/.local/share/ml-referee/proj1"
    assert policy.workspace == tmp_path / "ws"


DENIED_CMDS = [
    "cat {store}/holdout.parquet",
    "cat $HOME/.local/share/ml-referee/*/holdout*",
    "cat ~/.local/share/ml-referee/proj1/manifest.json",
    "cd {store} && ls",
    "ln -s {store} link && cat link/holdout.parquet",
    "python -c \"open('{store}/holdout.parquet')\"",
    "X={store}; cat $X/holdout.parquet",
    "cd sub && cat ../../home/.local/share/ml-referee/proj1/holdout.parquet",
    "cat ../../.local/share/ml-referee/proj1/holdout.parquet",
    "python -c \"import os;open(os.path.expanduser('~')"
    "+'/.local/share/ml-referee/proj1/holdout.parquet')\"",
]


@pytest.mark.parametrize("cmd", DENIED_CMDS)
def test_bash_touching_store_denied(cmd: str, policy: Policy) -> None:
    d = decide(bash(cmd.format(store=policy.store), policy), policy)
    assert d.allow is False
    assert d.reason


@pytest.mark.parametrize("cmd", DENIED_CMDS)
def test_touches_sealed_true_for_commands(cmd: str, policy: Policy) -> None:
    assert policy.touches_sealed(cmd.format(store=policy.store)) is True


def test_touches_sealed_false_for_workspace(policy: Policy) -> None:
    assert policy.touches_sealed("python train.py") is False
    assert policy.touches_sealed(str(policy.workspace / "train.py")) is False


@pytest.mark.parametrize("name", ["Read", "Write", "Edit"])
def test_file_tools_on_store_denied(name: str, policy: Policy) -> None:
    for fname in ("evaluator.py", "holdout.parquet", "ledger.jsonl"):
        ev = tool(name, "file_path", str(policy.store / fname), policy)
        d = decide(ev, policy)
        assert d.allow is False
        assert str(policy.store) in d.reason or "store" in d.reason.lower()


def test_edit_of_sealed_evaluator_denied(policy: Policy) -> None:
    ev = tool("Edit", "file_path", str(policy.store / "evaluator.py"), policy)
    assert decide(ev, policy).allow is False


def test_read_of_store_path_denied(policy: Policy) -> None:
    ev = tool("Read", "file_path", str(policy.store / "manifest.json"), policy)
    assert decide(ev, policy).allow is False


@pytest.mark.parametrize("name", ["Write", "Edit"])
def test_write_edit_of_workspace_config_denied(name: str, policy: Policy) -> None:
    ev = tool(name, "file_path", str(policy.workspace / ".ml-referee.toml"), policy)
    assert decide(ev, policy).allow is False


def test_grep_path_store_denied(policy: Policy) -> None:
    ev = tool("Grep", "path", str(policy.store), policy)
    assert decide(ev, policy).allow is False


def test_glob_pattern_under_store_denied(policy: Policy) -> None:
    ev = tool("Glob", "pattern", f"{policy.store}/*", policy)
    assert decide(ev, policy).allow is False


@pytest.mark.parametrize(
    "cmd", ["python train.py", "pytest -q", "ml-referee eval predictions.csv"]
)
def test_workspace_commands_allowed(cmd: str, policy: Policy) -> None:
    assert decide(bash(cmd, policy), policy).allow is True


def test_train_command_on_workspace_allowed(policy: Policy) -> None:
    assert decide(bash("python train.py", policy), policy).allow is True


def test_read_workspace_file_allowed(policy: Policy) -> None:
    ev = tool("Read", "file_path", str(policy.workspace / "train.py"), policy)
    assert decide(ev, policy).allow is True


def test_write_workspace_train_py_allowed(policy: Policy) -> None:
    ev = tool("Write", "file_path", str(policy.workspace / "train.py"), policy)
    assert decide(ev, policy).allow is True


def test_unknown_tool_name_without_paths_allowed(policy: Policy) -> None:
    ev: dict[str, object] = {"tool_name": "TodoWrite", "tool_input": {}, "cwd": "/"}
    assert decide(ev, policy).allow is True
