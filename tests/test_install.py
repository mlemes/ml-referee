import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import ml_referee
from ml_referee import install as install_mod
from ml_referee.cli import main
from ml_referee.harness import HARNESSES
from ml_referee.install import Change, install

PY = "/opt/py/bin/python"
JSON_HARNESSES = ("codex", "devin", "agy")
PATHS = {
    "codex": ".codex/hooks.json",
    "devin": ".devin/hooks.v1.json",
    "agy": ".agents/hooks.json",
}
POST_MATCHER = {"codex": "^Bash$", "devin": "^exec$", "agy": "run_command"}


@pytest.fixture(autouse=True)
def plugin_src(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    src = tmp_path_factory.mktemp("plugin-src") / "ml-referee.js"
    src.write_text("// stub plugin\n", encoding="utf-8")
    monkeypatch.setattr(install_mod, "OPENCODE_PLUGIN_SRC", src)
    return src


def _events(harness: str, data: dict[str, Any]) -> dict[str, Any]:
    if harness == "codex":
        return data["hooks"]  # type: ignore[no-any-return]
    if harness == "agy":
        return data["ml-referee"]  # type: ignore[no-any-return]
    return data


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _expected_post_entry(harness: str, name: str, python: str) -> dict[str, Any]:
    return {
        "matcher": POST_MATCHER[harness],
        "hooks": [
            {
                "type": "command",
                "command": f"{python} -m ml_referee.hooks.recorder --harness {name}",
                "timeout": 30,
            },
        ],
    }


@pytest.mark.parametrize("harness", JSON_HARNESSES)
def test_creates_file_with_documented_shape(harness: str, tmp_path: Path) -> None:
    changes = install(harness, tmp_path, python=PY)
    target = tmp_path / PATHS[harness]
    assert changes == [
        Change(path=target, action="create"),
        Change(path=tmp_path / "AGENTS.md", action="create"),
    ]
    data = json.loads(target.read_text(encoding="utf-8"))
    events = _events(harness, data)
    if harness == "codex":
        assert set(data) == {"hooks"}
    if harness == "agy":
        assert set(data) == {"ml-referee"}
        assert events["enabled"] is True
    pre = events["PreToolUse"]
    post = events["PostToolUse"]
    assert len(pre) == 1
    assert len(post) == 1
    assert pre[0]["matcher"] == ""
    assert len(pre[0]["hooks"]) == 1
    pre_hook = pre[0]["hooks"][0]
    assert pre_hook["type"] == "command"
    assert pre_hook["timeout"] == 30
    assert f"-m ml_referee.hooks.guard --harness {harness}" in pre_hook["command"]
    assert pre_hook["command"].startswith("env PYTHONPATH=")
    assert PY in pre_hook["command"]
    assert "ml-referee" in pre_hook["command"]
    assert post[0]["matcher"] == POST_MATCHER[harness]
    post_hook = post[0]["hooks"][0]
    assert post_hook["type"] == "command"
    assert post_hook["timeout"] == 30
    assert f"-m ml_referee.hooks.recorder --harness {harness}" in post_hook["command"]
    assert post_hook["command"].startswith("env PYTHONPATH=")
    assert PY in post_hook["command"]
    assert "ml-referee" in post_hook["command"]


@pytest.mark.parametrize("harness", JSON_HARNESSES)
def test_defaults_to_sys_executable(harness: str, tmp_path: Path) -> None:
    install(harness, tmp_path)
    data = json.loads((tmp_path / PATHS[harness]).read_text(encoding="utf-8"))
    events = _events(harness, data)
    for event in ("PreToolUse", "PostToolUse"):
        command = events[event][0]["hooks"][0]["command"]
        assert sys.executable in command
        assert "ml-referee" in command


@pytest.mark.parametrize("harness", JSON_HARNESSES)
def test_second_run_is_unchanged(harness: str, tmp_path: Path) -> None:
    install(harness, tmp_path, python=PY)
    before = _snapshot(tmp_path)
    changes = install(harness, tmp_path, python=PY)
    assert changes == [
        Change(path=tmp_path / PATHS[harness], action="unchanged"),
        Change(path=tmp_path / "AGENTS.md", action="unchanged"),
    ]
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("harness", JSON_HARNESSES)
def test_merge_preserves_user_hooks(harness: str, tmp_path: Path) -> None:
    user_pre = {
        "matcher": "^Edit$",
        "hooks": [{"type": "command", "command": "echo mine", "timeout": 5}],
    }
    user_stop = [{"matcher": "", "hooks": [{"type": "command", "command": "echo s"}]}]
    if harness == "codex":
        existing: dict[str, Any] = {
            "hooks": {"PreToolUse": [user_pre], "Stop": user_stop},
            "other": 1,
        }
    elif harness == "agy":
        existing = {
            "mine": {"enabled": True, "PreToolUse": [user_pre]},
            "ml-referee": {"Stop": user_stop},
        }
    else:
        existing = {"PreToolUse": [user_pre], "Stop": user_stop, "other": 1}
    target = tmp_path / PATHS[harness]
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(existing), encoding="utf-8")

    changes = install(harness, tmp_path, python=PY)

    assert changes == [
        Change(path=target, action="merge"),
        Change(path=tmp_path / "AGENTS.md", action="create"),
    ]
    data = json.loads(target.read_text(encoding="utf-8"))
    events = _events(harness, data)
    if harness == "agy":
        assert data["mine"] == existing["mine"]
        assert events["enabled"] is True
    else:
        assert data.get("other") == existing.get("other")
    assert events["Stop"] == user_stop
    if harness == "agy":
        assert events["PreToolUse"][0]["matcher"] == ""
        assert len(events["PreToolUse"]) == 1
    else:
        assert events["PreToolUse"][0] == user_pre
        assert len(events["PreToolUse"]) == 2
        guard = events["PreToolUse"][1]["hooks"][0]["command"]
        assert f"-m ml_referee.hooks.guard --harness {harness}" in guard
        assert "ml-referee" in guard
    post = events["PostToolUse"]
    assert len(post) == 1
    expected = _expected_post_entry(harness, harness, PY)
    assert post[0]["matcher"] == expected["matcher"]
    assert len(post[0]["hooks"]) == 1
    post_hook = post[0]["hooks"][0]
    assert post_hook["type"] == "command"
    assert post_hook["timeout"] == 30
    assert "ml-referee" in post_hook["command"]
    assert f"-m ml_referee.hooks.recorder --harness {harness}" in post_hook["command"]
    # merging again is idempotent
    assert install(harness, tmp_path, python=PY)[0].action == "unchanged"


@pytest.mark.parametrize("harness", ["codex", "devin"])
def test_merge_keeps_user_entries_in_same_event(harness: str, tmp_path: Path) -> None:
    user_post = {
        "matcher": "^Bash$",
        "hooks": [{"type": "command", "command": "echo post"}],
    }
    existing: dict[str, Any] = {"PostToolUse": [user_post]}
    if harness == "codex":
        existing = {"hooks": existing}
    target = tmp_path / PATHS[harness]
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(existing), encoding="utf-8")
    install(harness, tmp_path, python=PY)
    events = _events(harness, json.loads(target.read_text(encoding="utf-8")))
    assert events["PostToolUse"][0] == user_post
    assert len(events["PostToolUse"]) == 2
    assert "ml-referee" in events["PostToolUse"][1]["hooks"][0]["command"]


@pytest.mark.parametrize("harness", JSON_HARNESSES)
def test_dry_run_writes_nothing(harness: str, tmp_path: Path) -> None:
    changes = install(harness, tmp_path, dry_run=True, python=PY)
    assert changes == [
        Change(path=tmp_path / PATHS[harness], action="create"),
        Change(path=tmp_path / "AGENTS.md", action="create"),
    ]
    assert _snapshot(tmp_path) == {}


@pytest.mark.parametrize("harness", JSON_HARNESSES)
def test_dry_run_merge_leaves_file_untouched(harness: str, tmp_path: Path) -> None:
    target = tmp_path / PATHS[harness]
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    changes = install(harness, tmp_path, dry_run=True, python=PY)
    assert changes == [
        Change(path=target, action="merge"),
        Change(path=tmp_path / "AGENTS.md", action="create"),
    ]
    assert target.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize("harness", JSON_HARNESSES)
def test_invalid_json_raises_without_overwrite(harness: str, tmp_path: Path) -> None:
    target = tmp_path / PATHS[harness]
    target.parent.mkdir(parents=True)
    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):  # noqa: PT011
        install(harness, tmp_path, python=PY)
    assert target.read_text(encoding="utf-8") == "{not json"


def test_opencode_copies_plugin(tmp_path: Path, plugin_src: Path) -> None:
    changes = install("opencode", tmp_path)
    target = tmp_path / ".opencode" / "plugins" / "ml-referee.js"
    agents = tmp_path / "AGENTS.md"
    assert changes == [
        Change(path=target, action="create"),
        Change(path=agents, action="create"),
    ]
    assert target.read_text(encoding="utf-8") == "// stub plugin\n"
    again = install("opencode", tmp_path)
    assert again == [
        Change(path=target, action="unchanged"),
        Change(path=agents, action="unchanged"),
    ]
    plugin_src.write_text("// v2\n", encoding="utf-8")
    updated = install("opencode", tmp_path)
    assert updated[0].action == "merge"
    assert target.read_text(encoding="utf-8") == "// v2\n"


def test_opencode_dry_run_writes_nothing(tmp_path: Path) -> None:
    changes = install("opencode", tmp_path, dry_run=True)
    assert changes == [
        Change(path=tmp_path / ".opencode" / "plugins" / "ml-referee.js", action="create"),
        Change(path=tmp_path / "AGENTS.md", action="create"),
    ]
    assert _snapshot(tmp_path) == {}


def test_claude_returns_empty_and_changes_nothing(tmp_path: Path) -> None:
    assert install("claude", tmp_path) == []
    assert install("claude", tmp_path, dry_run=True) == []
    assert _snapshot(tmp_path) == {}


def test_unknown_harness_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        install("nope", tmp_path)


def test_cli_install_creates_file(tmp_path: Path) -> None:
    assert main(["install", "--harness", "codex", "--root", str(tmp_path)]) == 0
    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert "ml-referee" in data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def test_cli_install_dry_run(tmp_path: Path) -> None:
    code = main(["install", "--harness", "codex", "--root", str(tmp_path), "--dry-run"])
    assert code == 0
    assert _snapshot(tmp_path) == {}


def test_cli_install_all_covers_every_harness(tmp_path: Path) -> None:
    assert main(["install", "--harness", "all", "--root", str(tmp_path)]) == 0
    files = set(_snapshot(tmp_path))
    assert files == {*PATHS.values(), ".opencode/plugins/ml-referee.js", "AGENTS.md"}
    assert set(HARNESSES) == {"claude", "codex", "devin", "agy", "opencode"}


def test_cli_install_all_dry_run(tmp_path: Path) -> None:
    code = main(["install", "--harness", "all", "--root", str(tmp_path), "--dry-run"])
    assert code == 0
    assert _snapshot(tmp_path) == {}


PKG_DIR = str(Path(ml_referee.__file__).resolve().parent.parent)


def _command(harness: str, root: Path, event: str) -> str:
    data = json.loads((root / PATHS[harness]).read_text(encoding="utf-8"))
    return str(_events(harness, data)[event][0]["hooks"][0]["command"])


@pytest.mark.parametrize("harness", JSON_HARNESSES)
def test_default_commands_prefix_pythonpath(harness: str, tmp_path: Path) -> None:
    install(harness, tmp_path)
    prefix = f"env PYTHONPATH={shlex.quote(PKG_DIR)} {sys.executable} -m ml_referee.hooks."
    guard = _command(harness, tmp_path, "PreToolUse")
    rec = _command(harness, tmp_path, "PostToolUse")
    assert guard.startswith(prefix + f"guard --harness {harness}")
    assert rec.startswith(prefix + f"recorder --harness {harness}")


@pytest.mark.parametrize("harness", JSON_HARNESSES)
def test_explicit_python_keeps_interpreter(harness: str, tmp_path: Path) -> None:
    install(harness, tmp_path, python="/opt/custom/bin/python")
    for event in ("PreToolUse", "PostToolUse"):
        command = _command(harness, tmp_path, event)
        assert "/opt/custom/bin/python -m ml_referee.hooks." in command


@pytest.mark.parametrize("harness", JSON_HARNESSES)
def test_old_entry_without_prefix_is_replaced(harness: str, tmp_path: Path) -> None:
    old = f"{sys.executable} -m ml_referee.hooks.guard --harness {harness} # ml-referee"
    old_entry = {"matcher": "", "hooks": [{"type": "command", "command": old}]}
    existing: dict[str, Any] = {"PreToolUse": [old_entry]}
    if harness == "codex":
        existing = {"hooks": existing}
    elif harness == "agy":
        existing = {"ml-referee": existing}
    target = tmp_path / PATHS[harness]
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(existing), encoding="utf-8")
    install(harness, tmp_path)
    events = _events(harness, json.loads(target.read_text(encoding="utf-8")))
    assert len(events["PreToolUse"]) == 1
    assert events["PreToolUse"][0]["hooks"][0]["command"].startswith("env PYTHONPATH=")
    assert install(harness, tmp_path)[0].action == "unchanged"


def test_generated_codex_guard_runs_without_inherited_pythonpath(tmp_path: Path) -> None:
    install("codex", tmp_path)
    command = _command("codex", tmp_path, "PreToolUse")
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "python train.py"},
        "cwd": str(tmp_path),
    }
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = subprocess.run(  # noqa: S602
        command,
        shell=True,
        env={"PATH": os.environ["PATH"]},
        input=json.dumps(event),
        text=True,
        capture_output=True,
        cwd=elsewhere,
        check=False,
    )
    assert result.returncode == 0, result.stderr
