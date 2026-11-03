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
        'PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/src" python3 -m ml_referee.hooks.guard --harness claude'
        in _commands(pre[0])
    )
    for event in ("PostToolUse", "PostToolUseFailure"):
        entry = hooks[event][0]
        assert "Bash" in str(entry["matcher"])
        assert any(
            c.endswith("-m ml_referee.hooks.recorder --harness claude")
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


# --- multi-harness hardening ---

import shutil  # noqa: E402

PACKAGED_JS = ROOT / "src" / "ml_referee" / "data" / "opencode" / "ml-referee.js"
SRC_DIR = ROOT / "src"


def test_claude_hooks_pass_harness_claude() -> None:
    hooks = _hooks()
    for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        for entry in hooks[event]:
            for command in _commands(entry):
                assert "--harness claude" in command, (event, command)


def test_claude_hook_with_permission_mode_still_denies(ws: tuple[Path, Path, Path]) -> None:
    work, store, xdg = ws
    command = _commands(_hooks()["PreToolUse"][0])[0]
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": f"cat {store}/holdout.parquet"},
        "cwd": str(work),
        "permission_mode": "default",
    }
    proc = _run_hook(command, event, work, xdg)
    assert proc.returncode == 0
    parsed = json.loads(proc.stdout)
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert proc.stdout == json.dumps(parsed) + "\n"


def test_claude_recorder_hook_records_exit_code(ws: tuple[Path, Path, Path]) -> None:
    work, store, xdg = ws
    command = _commands(_hooks()["PostToolUse"][0])[0]
    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "python train.py --lr 1"},
        "tool_response": {"stdout": "", "exit_code": 4},
        "cwd": str(work),
        "permission_mode": "default",
    }
    assert _run_hook(command, event, work, xdg).returncode == 0
    records = Ledger(store / "ledger.jsonl").records()
    assert [r.payload["exit_code"] for r in records] == [4]


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

_DRIVER = """\
import { pathToFileURL } from 'node:url'
const mod = await import(pathToFileURL(process.argv[2]).href)
const hooks = await mod.MlReferee({ directory: process.argv[3] })
const mode = process.argv[4]
if (mode === 'before-noargs') {
  await hooks['tool.execute.before']({ tool: 'mcp_ping' }, {})
} else if (mode === 'after-metadata') {
  await hooks['tool.execute.after'](
    { tool: 'bash', args: { command: 'python train.py' } },
    { metadata: { exitCode: 0 }, exitCode: 7 },
  )
} else if (mode === 'after-top') {
  await hooks['tool.execute.after'](
    { tool: 'bash', args: { command: 'python train.py' } },
    { exitCode: 5 },
  )
}
"""


def _fake_python(tmp_path: Path) -> tuple[Path, Path]:
    logs = tmp_path / "logs"
    logs.mkdir()
    fake = tmp_path / "fakepy"
    fake.write_text(
        '#!/bin/sh\ncase "$*" in *recorder*) f=recorder;; *) f=guard;; esac\n'
        f'cat > "{logs}/$f.json"\nexit 0\n'
    )
    fake.chmod(0o755)
    return fake, logs


def _run_plugin(tmp_path: Path, mode: str) -> Path:
    from ml_referee import install as install_mod  # noqa: PLC0415
    from ml_referee.install import install  # noqa: PLC0415

    fake, logs = _fake_python(tmp_path)
    root = tmp_path / "proj"
    root.mkdir()
    old = install_mod.OPENCODE_PLUGIN_SRC
    install_mod.OPENCODE_PLUGIN_SRC = PACKAGED_JS
    try:
        install("opencode", root, python=str(fake))
    finally:
        install_mod.OPENCODE_PLUGIN_SRC = old
    plugin = tmp_path / "plugin.mjs"
    plugin.write_text((root / ".opencode" / "plugins" / "ml-referee.js").read_text())
    driver = tmp_path / "driver.mjs"
    driver.write_text(_DRIVER)
    proc = subprocess.run(  # noqa: S603
        ["node", str(driver), str(plugin), str(root), mode],  # noqa: S607
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return logs


@needs_node
def test_js_plugin_before_without_output_args_does_not_crash(tmp_path: Path) -> None:
    logs = _run_plugin(tmp_path, "before-noargs")
    sent = json.loads((logs / "guard.json").read_text())
    assert sent["tool_name"] == "mcp_ping"
    assert sent["tool_input"] == {}


@needs_node
def test_js_plugin_after_forwards_metadata_exit_code(tmp_path: Path) -> None:
    logs = _run_plugin(tmp_path, "after-metadata")
    sent = json.loads((logs / "recorder.json").read_text())
    assert sent["exit_code"] == 0
    assert sent["tool_input"] == {"command": "python train.py"}


@needs_node
def test_js_plugin_after_forwards_top_level_exit_code(tmp_path: Path) -> None:
    logs = _run_plugin(tmp_path, "after-top")
    sent = json.loads((logs / "recorder.json").read_text())
    assert sent["exit_code"] == 5
