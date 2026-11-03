import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ml_referee.config import load_config, store_dir
from ml_referee.ledger import Ledger

ROOT = Path(__file__).resolve().parent.parent

TOML = """[project]
id = "plugproj"

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


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text()
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, f"no frontmatter in {path}"
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _hooks() -> dict[str, list[dict[str, object]]]:
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    assert "hooks" in data
    hooks: dict[str, list[dict[str, object]]] = data["hooks"]
    return hooks


def _commands(entry: dict[str, object]) -> list[str]:
    inner = entry["hooks"]
    assert isinstance(inner, list)
    return [str(h["command"]) for h in inner]


def test_hooks_json_registers_guard_and_recorder() -> None:
    hooks = _hooks()
    pre = hooks["PreToolUse"]
    assert len(pre) >= 1
    matcher = str(pre[0]["matcher"])
    for tool in ("Read", "Write", "Edit", "Bash", "Grep", "Glob"):
        assert tool in matcher
    assert (
        'PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/src" python3 -m ml_referee.hooks.guard'
        in _commands(pre[0])
    )
    for event in ("PostToolUse", "PostToolUseFailure"):
        entry = hooks[event][0]
        assert "Bash" in str(entry["matcher"])
        assert any(
            c.endswith("-m ml_referee.hooks.recorder")
            and "${CLAUDE_PLUGIN_ROOT}/src" in c
            for c in _commands(entry)
        )


def _run_hook(command: str, event: dict[str, object], cwd: Path, xdg: Path):
    cmd = command.replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT))
    env = {**os.environ, "XDG_DATA_HOME": str(xdg)}
    env.pop("PYTHONPATH", None)
    return subprocess.run(  # noqa: S602
        cmd, shell=True, input=json.dumps(event), text=True, env=env,  # noqa: S604
        cwd=cwd, capture_output=True, check=False,
    )


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    work = tmp_path / "w"
    work.mkdir()
    (work / "train.csv").write_text("a,y\n1,0\n")
    (work / ".ml-referee.toml").write_text(TOML)
    cfg = load_config(work / ".ml-referee.toml")
    store = store_dir(cfg)
    store.mkdir(parents=True)
    for name in ("holdout.parquet", "evaluator.py", "manifest.json"):
        (store / name).write_text("x")
    return work, store, xdg


def test_guard_command_denies_store_access(ws: tuple[Path, Path, Path]) -> None:
    work, store, xdg = ws
    command = _commands(_hooks()["PreToolUse"][0])[0]
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": f"cat {store}/holdout.parquet"},
        "cwd": str(work),
    }
    proc = _run_hook(command, event, work, xdg)
    assert proc.returncode == 0
    spec = json.loads(proc.stdout)["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"


@pytest.mark.parametrize("hook_event", ["PostToolUse", "PostToolUseFailure"])
def test_recorder_command_appends_trial(
    ws: tuple[Path, Path, Path], hook_event: str
) -> None:
    work, store, xdg = ws
    entry = _hooks()[hook_event][0]
    command = _commands(entry)[0]
    event = {
        "hook_event_name": hook_event,
        "tool_name": "Bash",
        "tool_input": {"command": "python train.py --lr 1"},
        "tool_response": {"stdout": "ok", "exit_code": 0},
        "cwd": str(work),
    }
    proc = _run_hook(command, event, work, xdg)
    assert proc.returncode == 0
    assert Ledger(store / "ledger.jsonl").trial_count() == 1


@pytest.mark.parametrize("name", ["ml-seal", "ml-audit", "ml-claim"])
def test_command_files_have_frontmatter(name: str) -> None:
    fm = _frontmatter(ROOT / "commands" / f"{name}.md")
    assert fm.get("description")
    assert fm.get("allowed-tools")


def test_skill_frontmatter() -> None:
    fm = _frontmatter(ROOT / "skills" / "ml-referee" / "SKILL.md")
    assert fm["name"] == "ml-referee"
    assert fm.get("description")


def test_reviewer_agent_frontmatter() -> None:
    fm = _frontmatter(ROOT / "agents" / "ml-reviewer.md")
    assert fm["name"] == "ml-reviewer"
    assert fm.get("description")
    tools = {t.strip() for t in fm["tools"].split(",")}
    assert tools == {"Read", "Grep", "Glob"}


def test_plugin_manifest_name() -> None:
    data = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert data["name"] == "ml-referee"
