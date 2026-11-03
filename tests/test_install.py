import json
import sys
from pathlib import Path
from typing import Any

import pytest

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
    assert changes == [Change(path=target, action="create")]
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
    assert pre_hook["command"].startswith(PY)
    assert "ml-referee" in pre_hook["command"]
    assert post[0]["matcher"] == POST_MATCHER[harness]
    post_hook = post[0]["hooks"][0]
    assert post_hook["type"] == "command"
    assert post_hook["timeout"] == 30
    assert f"-m ml_referee.hooks.recorder --harness {harness}" in post_hook["command"]
    assert post_hook["command"].startswith(PY)
    assert "ml-referee" in post_hook["command"]


@pytest.mark.parametrize("harness", JSON_HARNESSES)
def test_defaults_to_sys_executable(harness: str, tmp_path: Path) -> None:
    install(harness, tmp_path)
    data = json.loads((tmp_path / PATHS[harness]).read_text(encoding="utf-8"))
    events = _events(harness, data)
    for event in ("PreToolUse", "PostToolUse"):
        command = events[event][0]["hooks"][0]["command"]
        assert command.startswith(sys.executable)
        assert "ml-referee" in command


@pytest.mark.parametrize("harness", JSON_HARNESSES)
def test_second_run_is_unchanged(harness: str, tmp_path: Path) -> None:
    install(harness, tmp_path, python=PY)
    before = _snapshot(tmp_path)
    changes = install(harness, tmp_path, python=PY)
    assert changes == [Change(path=tmp_path / PATHS[harness], action="unchanged")]
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

    assert changes == [Change(path=target, action="merge")]
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
    assert changes == [Change(path=tmp_path / PATHS[harness], action="create")]
    assert _snapshot(tmp_path) == {}


@pytest.mark.parametrize("harness", JSON_HARNESSES)
def test_dry_run_merge_leaves_file_untouched(harness: str, tmp_path: Path) -> None:
    target = tmp_path / PATHS[harness]
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    changes = install(harness, tmp_path, dry_run=True, python=PY)
    assert changes == [Change(path=target, action="merge")]
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
    assert changes == [Change(path=target, action="create")]
    assert target.read_text(encoding="utf-8") == "// stub plugin\n"
    again = install("opencode", tmp_path)
    assert again == [Change(path=target, action="unchanged")]
    plugin_src.write_text("// v2\n", encoding="utf-8")
    updated = install("opencode", tmp_path)
    assert updated[0].action == "merge"
    assert target.read_text(encoding="utf-8") == "// v2\n"


def test_opencode_dry_run_writes_nothing(tmp_path: Path) -> None:
    changes = install("opencode", tmp_path, dry_run=True)
    assert changes == [
        Change(path=tmp_path / ".opencode" / "plugins" / "ml-referee.js", action="create"),
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
    assert files == {*PATHS.values(), ".opencode/plugins/ml-referee.js"}
    assert set(HARNESSES) == {"claude", "codex", "devin", "agy", "opencode"}


def test_cli_install_all_dry_run(tmp_path: Path) -> None:
    code = main(["install", "--harness", "all", "--root", str(tmp_path), "--dry-run"])
    assert code == 0
    assert _snapshot(tmp_path) == {}
