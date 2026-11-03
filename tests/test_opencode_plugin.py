import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ml_referee.config import load_config, store_dir

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "src" / "ml_referee" / "data" / "opencode" / "ml-referee.js"


def _has_node() -> bool:
    if shutil.which("node") is None:
        return False
    return subprocess.run(["node", "--version"], capture_output=True).returncode == 0  # noqa: S607


pytestmark = pytest.mark.skipif(not _has_node(), reason="node is not available")

TOML = """
[project]
id = "projoc"

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

HARNESS = """
import { pathToFileURL } from 'node:url'
const [pluginPath, directory, command] = process.argv.slice(2)
const mod = await import(pathToFileURL(pluginPath).href)
const hooks = await mod.MlReferee({ directory })
try {
  await hooks['tool.execute.before'](
    { tool: 'bash' },
    { args: { command } },
  )
  console.log(JSON.stringify({ outcome: 'resolved' }))
} catch (err) {
  console.log(JSON.stringify({ outcome: 'threw', isError: err instanceof Error, message: String(err.message) }))
}
"""


@pytest.fixture
def sealed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
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
    script = tmp_path / "harness.mjs"
    script.write_text(HARNESS)
    return ws, store, script


def _run(script: Path, ws: Path, command: str) -> dict[str, object]:
    venv_bin = ROOT / ".venv" / "bin"
    import os

    env = dict(os.environ)
    env["PATH"] = f"{venv_bin}{os.pathsep}{env['PATH']}"
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run(  # noqa: S603
        ["node", str(script), str(PLUGIN), str(ws), command],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    result: dict[str, object] = json.loads(proc.stdout.strip().splitlines()[-1])
    return result


def test_before_hook_throws_error_on_store_access(sealed: tuple[Path, Path, Path]) -> None:
    ws, store, script = sealed
    result = _run(script, ws, f"cat {store}/holdout.parquet")
    assert result["outcome"] == "threw"
    assert result["isError"] is True
    assert str(result["message"]).strip() != ""
    assert result["message"] != "NotImplemented"


def test_before_hook_resolves_on_normal_command(sealed: tuple[Path, Path, Path]) -> None:
    ws, _store, script = sealed
    result = _run(script, ws, "python train.py")
    assert result == {"outcome": "resolved"}


def test_plugin_exports_both_hooks(sealed: tuple[Path, Path, Path], tmp_path: Path) -> None:
    ws, _store, _script = sealed
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        "import { pathToFileURL } from 'node:url'\n"
        "const mod = await import(pathToFileURL(process.argv[2]).href)\n"
        "const hooks = await mod.MlReferee({ directory: process.argv[3] })\n"
        "console.log(JSON.stringify([typeof hooks['tool.execute.before'],"
        " typeof hooks['tool.execute.after']]))\n"
    )
    proc = subprocess.run(  # noqa: S603
        ["node", str(probe), str(PLUGIN), str(ws)],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert json.loads(proc.stdout) == ["function", "function"]
