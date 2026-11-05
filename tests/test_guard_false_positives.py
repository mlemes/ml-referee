"""Benign calls the guard rejected in E7 stay allowed; store access stays denied.

The layout mirrors the experiment: the workspace and the sealed store sit in
sibling directories (``work`` and ``state/ml-referee/<id>``).
"""

import copy
import json
from pathlib import Path

import pytest

from ml_referee.config import load_config, store_dir
from ml_referee.hooks.guard import _evaluate
from ml_referee.shellparse import split_heredocs

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "harness"
TOML = """[project]
id = "fp"

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
HARNESSES = ("claude", "codex", "devin", "agy", "opencode")
BASH = {
    "claude": "claude_bash",
    "codex": "codex_bash",
    "devin": "devin_exec",
    "agy": "agy_bash",
    "opencode": "opencode_bash",
}
WRITE = {
    "claude": "claude_write",
    "codex": "codex_apply_patch",
    "devin": "devin_write",
    "agy": "agy_write",
    "opencode": "opencode_write",
}
EDIT = {
    "claude": "claude_edit",
    "codex": "codex_apply_patch",
    "devin": "devin_edit",
    "agy": "agy_edit",
    "opencode": "opencode_edit",
}


@pytest.fixture
def proj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    work = tmp_path / "work"
    work.mkdir()
    (work / "train.csv").write_text("a,y\n1,0\n")
    (work / "solution.py").write_text("x = 1\n")
    (work / ".ml-referee.toml").write_text(TOML)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("DEVIN_PROJECT_DIR", str(work))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(work)
    store = store_dir(load_config(work / ".ml-referee.toml"))
    store.mkdir(parents=True)
    for name in ("holdout.parquet", "evaluator.py", "manifest.json"):
        (store / name).write_text("x")
    return work, store


def _retarget(value: object, work: Path) -> object:
    if isinstance(value, str):
        if value == "/p" or value.startswith("/p/"):
            return str(work) + value[2:]
        return value
    if isinstance(value, list):
        return [_retarget(v, work) for v in value]
    if isinstance(value, dict):
        return {k: _retarget(v, work) for k, v in value.items()}
    return value


def _load(name: str, work: Path) -> dict[str, object]:
    raw = json.loads((FIXTURES / f"{name}.json").read_text())
    out = _retarget(copy.deepcopy(raw), work)
    assert isinstance(out, dict)
    return out


def _bash(harness: str, work: Path, command: str) -> dict[str, object]:
    payload = _load(BASH[harness], work)
    if harness == "agy":
        call = payload["toolCall"]
        assert isinstance(call, dict)
        call["args"]["CommandLine"] = command
        call["args"].pop("Cwd", None)
    else:
        payload["tool_input"]["command"] = command  # type: ignore[index]
    return payload


def _write(harness: str, work: Path, path: str, content: str) -> dict[str, object]:
    payload = _load(WRITE[harness], work)
    if harness == "codex":
        payload["tool_input"]["command"] = (  # type: ignore[index]
            f"*** Begin Patch\n*** Add File: {path}\n+{content}\n*** End Patch"
        )
    elif harness == "agy":
        args = payload["toolCall"]["args"]  # type: ignore[index]
        args["TargetFile"], args["CodeContent"] = path, content
    else:
        tin = payload["tool_input"]
        assert isinstance(tin, dict)
        key = "filePath" if harness == "opencode" else "file_path"
        tin[key], tin["content"] = path, content
    return payload


def _edit(harness: str, work: Path, path: str, text: str) -> dict[str, object]:
    payload = _load(EDIT[harness], work)
    if harness == "codex":
        payload["tool_input"]["command"] = (  # type: ignore[index]
            f"*** Begin Patch\n*** Update File: {path}\n@@\n-x\n+{text}\n*** End Patch"
        )
    elif harness == "agy":
        args = payload["toolCall"]["args"]  # type: ignore[index]
        args["TargetFile"], args["ReplacementContent"] = path, text
    else:
        tin = payload["tool_input"]
        assert isinstance(tin, dict)
        key = "filePath" if harness == "opencode" else "file_path"
        tin[key] = path
        tin["newString" if harness == "opencode" else "new_string"] = text
    return payload


def _allowed(harness: str, payload: dict[str, object]) -> bool:
    return _evaluate(harness, payload)[1].allow


HEREDOC_STAR = (
    "cat > solution.py <<'EOF'\nimport numpy as np\n"
    "pred = 0.5 * a + 0.5 * b\nEOF\npython solution.py\n"
)
HEREDOC_NAME = (
    "printf 'x\\n' >> results.tsv && cat > results.md <<'EOF'\n"
    "# Result\nThe ml-referee audit found nothing; .ml-referee.toml was not read.\n"
    "EOF\nml-referee audit --against train.csv 2>&1 | tail -4\n"
)

BENIGN_BASH = [
    HEREDOC_STAR,
    HEREDOC_NAME,
    'python -c "print(2 * 3)"; echo 2 * 3',
    "grep -r foo * | wc -l",
    "cat * | head -3 && python -c 'print(1)'",
    "ls ./*.csv ../work/* | sort",
]


@pytest.mark.parametrize("harness", HARNESSES)
@pytest.mark.parametrize("command", BENIGN_BASH)
def test_benign_bash_allowed(
    harness: str, command: str, proj: tuple[Path, Path],
) -> None:
    work, _ = proj
    assert _allowed(harness, _bash(harness, work, command))


@pytest.mark.parametrize("harness", HARNESSES)
@pytest.mark.parametrize(
    "content",
    [
        "# the ml-referee guard checks tool calls\nx = 1\n",
        "see `ml-referee audit --against test.csv` and sealed ml-referee eval",
        "The sealed store and .ml-referee.toml were not read.",
    ],
)
def test_content_naming_tool_allowed(
    harness: str, content: str, proj: tuple[Path, Path],
) -> None:
    work, _ = proj
    assert _allowed(harness, _write(harness, work, str(work / "solution.py"), content))
    assert _allowed(harness, _edit(harness, work, str(work / "solution.py"), content))


# Mirror forms: every relaxation above must not open the store or the config.
DENIED_BASH = [
    "cp .* /tmp/x",
    "cat .m* > /tmp/x",
    "cat [.]ml* | head",
    "cp ./.ml* /tmp/x",
    "cp ?ml-referee.toml .?ml* /tmp/x",
    "cp ./[.]* /tmp/x",
    "grep x .* | wc -l",
    "shopt -s dotglob; mv * /tmp/x",
    "BASHOPTS=dotglob mv * /tmp/x",
    "GLOBIGNORE=x mv * /tmp/x",
    "bash -O dotglob -c 'x'; mv * /tmp/x",
    "setopt glob_dots; mv * /tmp/x",
    "bash <<'EOF'\nshopt -s dotglob\nmv * /tmp/x\nEOF\n",
    "cat > solution.py <<'EOF'\nx\nEOF\nshopt -s dotglob; mv * /tmp/x",
    "cat > solution.py <<'EOF'\nx = 1\nEOF\nmv .* /tmp/x",
    "find . -name '*.toml' -delete",
    "find . -name \"*\" -delete",
    "printf -v GLOB\"\"IGNORE '*'; rm *.toml",
    "s'h'opt -s d'o'tglob; rm *.toml",
    "true \"$(echo \"\ncat <<'EOF'\n\"; cp evil .ml-referee.toml; ls\n)\" <<'EOF'\nEOF\n",
    "{\ncat <<'EOF'\nsed -i p .ml-referee.toml\nEOF\n} | sh",
    "cat {store}/*",
    "cat ../state/*/*/*",
    "cat ../state/ml-referee/*/holdout.parquet",
    "ls ../state/*",
    "cat {store}/holdout.parquet <<'EOF'\nx\nEOF\n",
    "cat > {store}/x <<'EOF'\nx\nEOF\n",
    "cat > solution.py <<'EOF'\ncat {store}/holdout.parquet\nEOF\n",
    "cat > solution.py <<'EOF'\n{store}\nEOF\n",
    "cat > solution.py <<'EOF'\nml-referee/fp/holdout.parquet\nEOF\n",
    "cat > solution.py <<'EOF'\n~/.local/share/ml-referee\nEOF\n",
    "cat > solution.py <<'EOF'\n$XDG_DATA_HOME/ml-referee\nEOF\n",
    "cat > x.sh <<EOF\n$(cat {store}/holdout.parquet)\nEOF\n",
    "cat > x.sh <<EOF\nml-referee ok\nEOF\n",
    "cat <<'EOF' | bash\ncat {store}/holdout.parquet\nEOF\n",
    "cat <<'EOF' | bash\ncd ../state; cat ml-referee/*/holdout.parquet\nEOF\n",
    "bash <<'EOF'\ncat ml-referee/fp/holdout.parquet\nEOF\n",
    "bash <<'EOF'\ncd ../state\nls ml-referee\nEOF\n",
    "python <<'EOF'\nprint(open('{store}/holdout.parquet').read())\nEOF\n",
    "echo \"<<'EOF'\"\ncat {store}/holdout.parquet\nEOF\n",
    "echo \"<<'EOF'\"\ncd ../state; cat ml-referee/*/hold*\nEOF\n",
    "cat <<'EOF' > x\nunterminated {store}",
    "cat > a <<'EOF'\nx\nEOF\ncat {store}/holdout.parquet",
    "cat > a <<'EOF'\nx\nEOF\ncd ../state && ls ml-referee",
]


@pytest.mark.parametrize("harness", HARNESSES)
@pytest.mark.parametrize("command", DENIED_BASH)
def test_store_and_config_access_denied(
    harness: str, command: str, proj: tuple[Path, Path],
) -> None:
    work, store = proj
    text = command.replace("{store}", str(store))
    assert not _allowed(harness, _bash(harness, work, text))


@pytest.mark.parametrize("harness", HARNESSES)
@pytest.mark.parametrize(
    "content",
    [
        "{store}/holdout.parquet",
        "path = '{store}'",
        "~/.local/share/ml-referee/fp",
        "$XDG_DATA_HOME/ml-referee/fp",
        "open('ml-referee/fp/holdout.parquet')",
        "cd ../state/ml-referee/fp",
    ],
)
def test_content_naming_store_path_denied(
    harness: str, content: str, proj: tuple[Path, Path],
) -> None:
    work, store = proj
    text = content.replace("{store}", str(store))
    assert not _allowed(harness, _write(harness, work, str(work / "solution.py"), text))
    assert not _allowed(harness, _edit(harness, work, str(work / "solution.py"), text))


@pytest.mark.parametrize("harness", HARNESSES)
def test_write_targets_denied(harness: str, proj: tuple[Path, Path]) -> None:
    work, store = proj
    for target in (store / "evaluator.py", store / "new.txt", work / ".ml-referee.toml"):
        assert not _allowed(harness, _write(harness, work, str(target), "x = 1"))
        assert not _allowed(harness, _edit(harness, work, str(target), "x = 1"))


def test_split_heredocs_extracts_inert_bodies() -> None:
    shell, bodies = split_heredocs(HEREDOC_STAR) or ("", [])
    assert bodies == ["import numpy as np\npred = 0.5 * a + 0.5 * b"]
    assert "*" not in shell
    assert "<<" not in shell
    assert "cat > solution.py" in shell
    assert "python solution.py" in shell


@pytest.mark.parametrize(
    "text",
    [
        "no heredoc here",
        "cat <<EOF\nx\nEOF\n",  # unquoted delimiter expands $(...)
        "cat <<'EOF' | sh\nx\nEOF\n",
        "cat <<'EOF'\nx\n",  # never terminated
        "python <<'EOF'\nx\nEOF\n",  # body is code
        "echo \"<<'EOF'\"\nx\nEOF\n",  # operator is quoted
        "cat <<-'EOF'\nx\nEOF\n",
        "cat <<'A' <<'B'\nx\nA\ny\nB\n",
        "cat <<'EOF' # c\nx\nEOF\n",
        "cat <<'EOF' > $(sh)\nx\nEOF\n",
        "cat <<'EOF' >(sh)\nx\nEOF\n",
        "cat <<< 'x'",
    ],
)
def test_split_heredocs_refuses_unsure_input(text: str) -> None:
    assert split_heredocs(text) is None


@pytest.mark.parametrize(
    "text",
    [
        'echo "x\ncat > a <<\'EOF\'\n" ; cat ../state/*/*/* ; echo "\nEOF\n"',
        "echo a \\\ncat > a <<'EOF'\nx\nEOF\n",
        "alias cat=bash\ncat <<'EOF'\nx\nEOF\n",
        "PATH=/tmp:$PATH\ncat <<'EOF'\nx\nEOF\n",
        "cat() { bash; }\ncat <<'EOF'\nx\nEOF\n",
        "echo $'a\\'\ncat > a <<'EOF'\nx\nEOF\n",
    ],
)
def test_split_heredocs_refuses_context_tricks(text: str) -> None:
    assert split_heredocs(text) is None
