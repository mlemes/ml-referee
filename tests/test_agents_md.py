from pathlib import Path

import pytest

from ml_referee import install as install_mod
from ml_referee.agents_md import apply_block, render_block
from ml_referee.install import Change, install

START = "<!-- ml-referee:start -->"
END = "<!-- ml-referee:end -->"


def test_render_block_markers_and_rules() -> None:
    block = render_block()
    assert block.startswith(START)
    assert block.rstrip().endswith(END)
    assert block.count(START) == 1
    assert block.count(END) == 1
    lowered = block.lower()
    assert "never" in lowered
    for word in ("read", "copy", "reference"):
        assert word in lowered
    assert ".ml-referee.toml" in block
    assert "sealed" in lowered
    assert "ml-referee claim" in block


def test_apply_block_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    change = apply_block(path)
    assert change == Change(path, "create")
    assert path.read_text(encoding="utf-8").strip() == render_block().strip()


def test_apply_block_merges_and_preserves_rest(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("# My rules\n\nUse tabs.\n", encoding="utf-8")
    change = apply_block(path)
    assert change == Change(path, "merge")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# My rules\n\nUse tabs.\n")
    assert render_block().strip() in text


def test_apply_block_rerun_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("# My rules\n", encoding="utf-8")
    apply_block(path)
    first = path.read_text(encoding="utf-8")
    change = apply_block(path)
    assert change == Change(path, "unchanged")
    assert path.read_text(encoding="utf-8") == first


def test_apply_block_replaces_in_place(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text(
        f"before\n\n{START}\nold stale text\n{END}\n\nafter\n", encoding="utf-8"
    )
    change = apply_block(path)
    assert change == Change(path, "merge")
    text = path.read_text(encoding="utf-8")
    assert "old stale text" not in text
    assert text.count(START) == 1
    assert text.count(END) == 1
    assert text.startswith("before\n\n" + START)
    assert text.rstrip().endswith("after")
    assert text.index("before") < text.index(START) < text.index(END) < text.index("after")
    assert render_block().strip() in text


@pytest.fixture(autouse=True)
def plugin_src(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    src = tmp_path_factory.mktemp("plugin-src") / "ml-referee.js"
    src.write_text("// stub plugin\n", encoding="utf-8")
    monkeypatch.setattr(install_mod, "OPENCODE_PLUGIN_SRC", src)
    return src


@pytest.mark.parametrize("harness", ["codex", "devin", "agy", "opencode"])
def test_install_writes_agents_md_block(harness: str, tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("keep me\n", encoding="utf-8")
    changes = install(harness, tmp_path, python="/opt/py/bin/python")
    agents = tmp_path / "AGENTS.md"
    assert any(c.path == agents for c in changes)
    text = agents.read_text(encoding="utf-8")
    assert text.startswith("keep me\n")
    assert START in text
    assert END in text


def test_install_creates_agents_md_when_missing(tmp_path: Path) -> None:
    install("codex", tmp_path, python="/opt/py/bin/python")
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert render_block().strip() in text


def test_install_dry_run_does_not_write_agents_md(tmp_path: Path) -> None:
    changes = install("codex", tmp_path, dry_run=True, python="/opt/py/bin/python")
    assert not (tmp_path / "AGENTS.md").exists()
    assert any(c.path == tmp_path / "AGENTS.md" for c in changes)


def test_claude_does_not_write_agents_md(tmp_path: Path) -> None:
    install("claude", tmp_path)
    assert not (tmp_path / "AGENTS.md").exists()
