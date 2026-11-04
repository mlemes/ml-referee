"""Guard config discovery and store-root rules when cwd is outside the workspace."""

import io
import json
from pathlib import Path

import pytest

from ml_referee.hooks.guard import _evaluate, main

TOML = """
[project]
id = "proj1"

[data]
train = "train.csv"
label = "y"

[eval]
command = "python evaluate.py"
metric = "auc"
higher_is_better = true

[trials]
patterns = ["python evaluate.py*"]
"""

HARNESSES = ("claude", "codex", "devin", "agy", "opencode")
BASH_NAMES = {
    "claude": "Bash",
    "codex": "Bash",
    "devin": "exec",
    "opencode": "bash",
}
READ_NAMES = {
    "claude": ("Read", "file_path"),
    "codex": ("Read", "file_path"),
    "devin": ("read", "file_path"),
    "opencode": ("read", "filePath"),
}


class Layout:
    """Throwaway home, workspace with config, sealed store and symlinks."""

    def __init__(self, base: Path) -> None:
        self.home = base / "home"
        self.ws = self.home / "proj"
        self.other = base / "unrelated"
        self.data = self.home / ".local" / "share"
        self.root = self.data / "ml-referee"
        self.store = self.root / "proj1"
        self.link = self.ws / "link"
        self.linkdir = self.ws / "linkdir"
        for path in (self.ws, self.other, self.store):
            path.mkdir(parents=True)
        (self.ws / ".ml-referee.toml").write_text(TOML)
        for name in ("holdout.parquet", "evaluator.py", "manifest.json"):
            (self.store / name).write_text("x")
        self.link.symlink_to(self.store)
        self.linkdir.symlink_to(self.root)


@pytest.fixture
def lay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Layout:
    layout = Layout(tmp_path)
    monkeypatch.setenv("HOME", str(layout.home))
    for var in ("XDG_DATA_HOME", "CLAUDE_PROJECT_DIR", "DEVIN_PROJECT_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(layout.other)
    return layout


def bash_payload(
    harness: str,
    command: str,
    cwd: Path | str,
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path | None = None,
) -> dict[str, object]:
    """Build the hook payload shape of ``harness`` for one shell command."""
    if harness == "agy":
        return {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": command, "Cwd": str(cwd)},
            },
            "workspacePaths": [str(workspace)] if workspace else [],
        }
    raw: dict[str, object] = {
        "hook_event_name": "PreToolUse",
        "tool_name": BASH_NAMES[harness],
        "tool_input": {"command": command},
        "cwd": str(cwd),
    }
    if harness == "codex":
        raw["turn_id"] = "t1"
    if harness == "devin":
        raw["session_id"] = "s1"
        # Devin has no payload cwd: the project dir env or process cwd is used.
        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(cwd))
    return raw


def denied(harness: str, raw: dict[str, object]) -> bool:
    return not _evaluate(harness, raw)[1].allow


CWD_CASES = ("store", "root", "data", "link", "linkdir", "store_dot")
COMMANDS = ("ls", "cat holdout.parquet", "python3 -c \"print(open('x').read())\"")


def cwd_of(lay: Layout, case: str) -> str:
    return {
        "store": str(lay.store),
        "root": str(lay.root),
        "data": str(lay.data),
        "link": str(lay.link),
        "linkdir": str(lay.linkdir),
        "store_dot": str(lay.store / "."),
    }[case]


@pytest.mark.parametrize("harness", HARNESSES)
@pytest.mark.parametrize("case", CWD_CASES)
@pytest.mark.parametrize("command", COMMANDS)
def test_cwd_in_store_denied_without_config(
    lay: Layout,
    monkeypatch: pytest.MonkeyPatch,
    harness: str,
    case: str,
    command: str,
) -> None:
    raw = bash_payload(harness, command, cwd_of(lay, case), monkeypatch, lay.other)
    assert denied(harness, raw)


@pytest.mark.parametrize("harness", HARNESSES)
@pytest.mark.parametrize("case", ["store", "root", "link"])
def test_cwd_in_store_denied_with_config_in_workspace(
    lay: Layout,
    monkeypatch: pytest.MonkeyPatch,
    harness: str,
    case: str,
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(lay.ws))
    raw = bash_payload(harness, "ls", cwd_of(lay, case), monkeypatch, lay.ws)
    assert denied(harness, raw)


@pytest.mark.parametrize("harness", HARNESSES)
def test_benign_controls_allowed(
    lay: Layout, monkeypatch: pytest.MonkeyPatch, harness: str,
) -> None:
    commands = (
        "ls -la",
        "python evaluate.py > run.log 2>&1",
        "git status",
        "cat README.md",
        "python3 -c 'print(1)'",
    )
    for cwd, workspace in ((lay.ws, lay.ws), (lay.other, lay.other)):
        for command in commands:
            raw = bash_payload(harness, command, cwd, monkeypatch, workspace)
            decision = _evaluate(harness, raw)[1]
            assert decision.allow, (harness, cwd, command, decision.reason)
    # Without a config the product name alone is not a store reference.
    raw = bash_payload(harness, "pip install ml-referee", lay.other, monkeypatch, lay.other)
    assert not denied(harness, raw)


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_home_cwd_relative_store_path_denied(
    lay: Layout, monkeypatch: pytest.MonkeyPatch, harness: str,
) -> None:
    for command in (
        "cat .local/share/ml-referee/proj1/holdout.parquet",
        "cd ~ && cat .local/share/ml-referee/proj1/holdout.parquet",
        "ls .local/share/ml-referee",
        "head -c 10 ./.local/share/ml-referee/proj1/evaluator.py",
    ):
        raw = bash_payload(harness, command, lay.home, monkeypatch, lay.other)
        assert denied(harness, raw), command
    raw = bash_payload(harness, "ls", lay.home, monkeypatch, lay.other)
    assert not denied(harness, raw)


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_data_dir_cwd_relative_store_path_denied(
    lay: Layout, monkeypatch: pytest.MonkeyPatch, harness: str,
) -> None:
    raw = bash_payload(
        harness, "cat ml-referee/proj1/holdout.parquet", lay.data, monkeypatch,
    )
    assert denied(harness, raw)


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_residual_cases_documented_as_allowed(
    lay: Layout, monkeypatch: pytest.MonkeyPatch, harness: str,
) -> None:
    """Shell variable splitting from $HOME is a documented residual."""
    command = "L=.lo; S=cal/sha; R=re; M=ml-ref; cat ${L}${S}${R}/${M}eree/proj1/x"
    raw = bash_payload(harness, command, lay.home, monkeypatch, lay.other)
    assert not denied(harness, raw)


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])
def test_absolute_store_path_denied_without_config(
    lay: Layout, monkeypatch: pytest.MonkeyPatch, harness: str,
) -> None:
    for command in (
        f"cat {lay.store}/holdout.parquet",
        "cat $HOME/.local/share/ml-referee/proj1/holdout.parquet",
        "cat ~/.local/share/ml-referee/proj1/holdout.parquet",
        "cat $XDG_DATA_HOME/ml-referee/proj1/holdout.parquet",
        f"cat {lay.link}/holdout.parquet",
        f"cat {lay.ws}/linkdir/proj1/holdout.parquet",
        f"cp {lay.store} /tmp/x",
    ):
        raw = bash_payload(harness, command, lay.other, monkeypatch, lay.other)
        assert denied(harness, raw), command


@pytest.mark.parametrize("harness", ["claude", "codex", "opencode", "devin"])
def test_file_tool_path_args_denied_without_config(
    lay: Layout, monkeypatch: pytest.MonkeyPatch, harness: str,
) -> None:
    name, key = READ_NAMES[harness]
    for path in (
        str(lay.store / "holdout.parquet"),
        str(lay.link / "holdout.parquet"),
        "../home/.local/share/ml-referee/proj1/holdout.parquet",
    ):
        raw: dict[str, object] = {
            "hook_event_name": "PreToolUse",
            "tool_name": name,
            "tool_input": {key: path},
            "cwd": str(lay.other),
        }
        if harness == "codex":
            raw["turn_id"] = "t1"
        if harness == "devin":
            monkeypatch.setenv("DEVIN_PROJECT_DIR", str(lay.other))
        assert denied(harness, raw), path
    ok: dict[str, object] = {
        "hook_event_name": "PreToolUse",
        "tool_name": name,
        "tool_input": {key: str(lay.other / "notes.txt")},
        "cwd": str(lay.other),
    }
    if harness == "codex":
        ok["turn_id"] = "t1"
    if harness == "devin":
        monkeypatch.setenv("DEVIN_PROJECT_DIR", str(lay.other))
    assert not denied(harness, ok)


def test_custom_xdg_store_root(lay: Layout, monkeypatch: pytest.MonkeyPatch) -> None:
    xdg = lay.other / "xdg"
    store = xdg / "ml-referee" / "p"
    store.mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    for cwd in (store, store.parent):
        raw = bash_payload("claude", "ls", cwd, monkeypatch)
        assert denied("claude", raw)
    raw = bash_payload("claude", f"cat {store}/x", lay.other, monkeypatch)
    assert denied("claude", raw)


def test_claude_project_dir_selects_config(
    lay: Layout, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(lay.ws))
    raw = bash_payload("claude", "cat .ml-referee.toml > /tmp/x", lay.other, monkeypatch)
    # Config-only rule (not a store-root rule) proves the config was found.
    assert denied("claude", raw)
    reason = _evaluate("claude", raw)[1].reason
    assert "No config" not in reason


def test_payload_workspace_field_selects_config(
    lay: Layout, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for field in ("workspace_roots", "workspaceRoots", "workspace_root", "project_dir"):
        value: object = [str(lay.ws)] if field.endswith("s") else str(lay.ws)
        raw = bash_payload("claude", "rm .ml-referee.toml", lay.other, monkeypatch)
        raw[field] = value
        assert denied("claude", raw), field


def test_walk_up_from_cwd_still_works(
    lay: Layout, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sub = lay.ws / "sub"
    sub.mkdir()
    raw = bash_payload("claude", "rm .ml-referee.toml", sub, monkeypatch)
    assert denied("claude", raw)


def test_agy_workspace_and_cwd_store(
    lay: Layout, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = bash_payload("agy", "ls", lay.store, monkeypatch, lay.ws)
    assert denied("agy", raw)
    raw = bash_payload("agy", "ls", lay.other, monkeypatch, lay.ws)
    assert not denied("agy", raw)


def test_unparseable_event_without_config_names_store_denied(
    lay: Layout,
) -> None:
    raw = {
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
        "turn_id": "t",
        "tool_input": {
            "command": f"*** Begin Patch\n*** Bogus: {lay.store}/x\n*** End Patch\n",
        },
        "cwd": str(lay.other),
    }
    assert denied("codex", raw)
    raw["tool_input"] = {"command": "*** Bogus: solution.py"}
    assert not denied("codex", raw)


def test_main_denies_cwd_in_store(
    lay: Layout, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    raw = bash_payload("claude", "ls", lay.store, monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(raw)))
    assert main() == 0
    data = json.loads(capsys.readouterr().out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
