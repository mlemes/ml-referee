"""The guard allows the plugin CLI in ordinary compound commands (and nothing else)."""

from pathlib import Path

import pytest

from ml_referee.hooks.guard import _evaluate
from ml_referee.shellparse import split_commands
from tests.test_guard_cwd import HARNESSES, TOML, bash_payload


class Lay:
    """Workspace with a config and a sealed store, under the default or XDG data dir."""

    def __init__(self, base: Path, *, xdg: bool) -> None:
        self.home = base / "home"
        self.ws = self.home / "proj"
        self.data = base / "state" if xdg else self.home / ".local" / "share"
        self.root = self.data / "ml-referee"
        self.store = self.root / "proj1"
        for path in (self.ws, self.store):
            path.mkdir(parents=True)
        (self.ws / ".ml-referee.toml").write_text(TOML)
        for name in ("holdout.parquet", "evaluator.py", "manifest.json"):
            (self.store / name).write_text("x")
        (self.ws / "link").symlink_to(self.store)
        (self.ws / "linkdir").symlink_to(self.root)
        (self.ws / "linkfile").symlink_to(self.store / "holdout.parquet")
        (self.ws / "test.csv").write_text("a\n1\n")
        (self.ws / "sub").mkdir()


@pytest.fixture(params=["home", "xdg"])
def lay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Lay:
    layout = Lay(tmp_path, xdg=request.param == "xdg")
    monkeypatch.setenv("HOME", str(layout.home))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("DEVIN_PROJECT_DIR", raising=False)
    if request.param == "xdg":
        monkeypatch.setenv("XDG_DATA_HOME", str(layout.data))
    else:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.chdir(layout.ws)
    return layout


def verdict(harness: str, command: str, lay: Lay, monkeypatch: pytest.MonkeyPatch) -> bool:
    """Return True when the guard allows ``command`` for ``harness``."""
    if harness == "claude":
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(lay.ws))
    raw = bash_payload(harness, command, lay.ws, monkeypatch, lay.ws)
    return _evaluate(harness, raw)[1].allow


# Command forms reconstructed from the E7 Haiku C3/C4 transcripts.
ALLOWED = (
    "ml-referee audit --against test.csv",
    "ml-referee audit --against test.csv 2>&1 | tail -40; echo EXIT $?",
    "ml-referee audit --against test.csv 2>&1 | tail -n 60",
    "(ml-referee audit --against test.csv 2>&1 | tail -n 40); echo \"exit=$?\"",
    "which ml-referee && ml-referee audit --against test.csv 2>&1 | tail -n 60",
    "timeout 110 ml-referee audit --against test.csv 2>&1 | tail -40;"
    " echo exit=${PIPESTATUS[0]}",
    "ml-referee audit --against test.csv 2>&1 | tail -n 40; echo exit=${PIPESTATUS[0]}",
    "ml-referee audit --against test.csv > audit.log 2>&1; echo exit=$?;"
    " head -c 3000 audit.log; echo; python timer.py 2>&1 | tail -1",
    "ml-referee audit --against test.csv > /tmp/audit.log 2>&1; echo exit=$?;"
    " tail -n 60 /tmp/audit.log",
    "(command -v ml-referee && ml-referee audit --against test.csv) > audit.log 2>&1;"
    " echo exit=$?; tail -n 40 audit.log",
    "(command -v ml-referee && ml-referee audit --against test.csv) 2>&1 | tail -n 40;"
    " echo \"exit=$?\"",
    "printf 'nogit\\t0.9\\tkeep\\tridge ~0.5 CV\\n' >> results.tsv && cat results.tsv"
    " && (command -v ml-referee && ml-referee audit --against test.csv) 2>&1 | tail -n 40",
    "python evaluate.py > run.log 2>&1; echo exit=$?; cat metrics.json; which ml-referee",
    "sed -i 's/^RIDGE_LAMBDA = 1.0/RIDGE_LAMBDA = 30.0/' solution.py"
    " && python evaluate.py > run.log 2>&1; cat metrics.json;"
    " ml-referee audit --against test.csv 2>&1 | tail -n 40",
    "ml-referee eval --predictions holdout_predictions.csv 2>&1 | tail -n 40;"
    " echo \"--- eval exit=${PIPESTATUS[0]}\"",
    "python -c \"import solution; solution.predict('train.csv', 'holdout_features.csv',"
    " 'holdout_predictions.csv')\" && ml-referee eval --predictions"
    " holdout_predictions.csv 2>&1 | tail -n 40",
    "ml-referee verify-ledger",
    "ml-referee audit --sources solution.py train.py --against test.csv",
    "ml-referee audit --against=test.csv",
    "cd sub && ml-referee audit --against ../test.csv",
    "cd . && ml-referee audit",
    "env FOO=1 ml-referee audit",
    "FOO=1 BAR=2 ml-referee audit --against test.csv",
    "time ml-referee audit",
    "nohup ml-referee audit > audit.log 2>&1 &",
    "uv run ml-referee audit",
    "uv run --no-sync ml-referee audit --against test.csv",
    "python -m ml_referee.cli audit --against test.csv",
    "python3 -m ml_referee audit",
    "python -u -m ml_referee audit",
    "ml-referee audit &> audit.log",
    "ml-referee audit | tee audit.log",
    "ml-referee audit 2> err.log",
    "ml-referee audit < /dev/null",
    "{ ml-referee audit; } 2>&1 | tail",
    "which ml-referee",
    "command -v ml-referee",
    "ml-referee --help",
    "ml-referee audit --help",
    "ml-referee eval -h",
    "ml-referee seal --help",
    "ml-referee claim --help",
    "ml-referee verify-ledger --help",
    "ml-referee eval --predict-cmd \"python predict.py {features} {out}\"",
    "ml-referee eval --predict-cmd='python predict.py {features} {out}' 2>&1 | tail",
)


@pytest.mark.parametrize("harness", HARNESSES)
@pytest.mark.parametrize("command", ALLOWED)
def test_plugin_cli_forms_allowed(
    lay: Lay, monkeypatch: pytest.MonkeyPatch, harness: str, command: str,
) -> None:
    assert verdict(harness, command, lay, monkeypatch), command


def mirror(lay: Lay) -> list[str]:
    store = str(lay.store)
    return [
        # store paths in CLI arguments
        f"ml-referee audit --against {store}/holdout.parquet",
        f"ml-referee audit --against={store}/holdout.parquet",
        f"ml-referee audit --sources {store}/evaluator.py",
        f"ml-referee eval --predictions {store}/holdout.parquet",
        f"ml-referee eval --predictions={store}/holdout.parquet",
        f"ml-referee audit --against {lay.root}",
        f"ml-referee audit --against {lay.data}",
        "ml-referee audit --against link/holdout.parquet",
        "ml-referee audit --against linkdir/proj1/evaluator.py",
        "ml-referee audit --against linkfile",
        "ml-referee audit --against 'link/*'",
        "ml-referee audit --against linkdir/*/holdout*",
        "ml-referee audit --against $HOME/.local/share/ml-referee/proj1/holdout.parquet",
        "ml-referee audit --against ~/.local/share/ml-referee/proj1/holdout.parquet",
        "ml-referee audit --against $X",
        "ml-referee audit --against ${X}/holdout.parquet",
        "ml-referee audit --against ml-referee/proj1/holdout.parquet",
        "ml-referee audit --against 'a{b,c}'",
        # substitution and nesting
        "cat $(ml-referee audit)",
        "cat `ml-referee audit`",
        f"cat $(ml-referee audit; cat {store}/holdout.parquet)",
        "ml-referee audit --against $(echo test.csv)",
        "ml-referee audit --against \"$(echo test.csv)\"",
        "ml-referee audit <(cat test.csv)",
        "ml-referee audit <<EOF\nx\nEOF",
        "ml-referee audit <<< x",
        "ml-referee audit --against $'test.csv'",
        "bash -c 'ml-referee audit'",
        f"bash -c 'ml-referee audit; cat {store}/holdout.parquet'",
        "sh -c \"ml-referee audit\"",
        "eval ml-referee audit",
        "eval \"ml-referee audit\"",
        "xargs ml-referee audit",
        "sudo ml-referee audit",
        "echo ml-referee",
        "echo \"run ml-referee audit\"",
        "ls ml-referee",
        "cat ml-referee/proj1/holdout.parquet",
        # pipes and redirects that reach the store or the config
        f"ml-referee audit | tee {store}/x",
        f"ml-referee audit | tee -a {store}/ledger.jsonl",
        f"ml-referee audit > {store}/x",
        f"ml-referee audit >> {store}/ledger.jsonl",
        f"ml-referee audit 2> {store}/err",
        f"ml-referee audit &> {store}/err",
        f"ml-referee audit >& {store}/err",
        f"ml-referee audit < {store}/holdout.parquet",
        f"(ml-referee audit) > {store}/x",
        f"ml-referee audit 2>&1 | tail > {store}/x",
        "ml-referee audit > link/x",
        "ml-referee audit > linkdir/proj1/x",
        "ml-referee audit > linkfile",
        "ml-referee audit >> linkfile",
        "ml-referee audit > .ml-referee.toml",
        "ml-referee audit >> .ml-referee.toml",
        "ml-referee audit > ./.ml-referee.toml",
        "ml-referee audit > .ml-ref*.toml",
        f"ml-referee audit > {lay.ws}/.ml-referee.toml",
        "ml-referee audit | tee .ml-referee.toml",
        "ml-referee audit > $OUT",
        "ml-referee audit > \"$OUT\"",
        "ml-referee audit > $HOME/.local/share/ml-referee/proj1/x",
        # other segments still use the store rules
        f"ml-referee audit; cat {store}/holdout.parquet",
        f"ml-referee audit && cat {store}/holdout.parquet",
        "ml-referee audit; cat link/holdout.parquet",
        "ml-referee audit; cat linkfile",
        "ml-referee audit; cat $HOME/.local/share/ml-referee/proj1/holdout.parquet",
        "ml-referee audit; rm .ml-referee.toml",
        "ml-referee audit && sed -i s/budget/9/ .ml-referee.toml",
        "ml-referee audit; echo x > .ml-referee.toml",
        f"cat {store}/holdout.parquet | ml-referee audit",
        f"ls {lay.root} && ml-referee audit",
        "ls ~/.local; ml-referee audit",
        "ls $XDG_DATA_HOME; ml-referee audit",
        # cd tracking
        f"cd {store} && ml-referee audit",
        f"cd {lay.root} && ml-referee audit --against proj1/holdout.parquet",
        "cd link && ml-referee audit --against holdout.parquet",
        "cd linkdir && ml-referee audit --against proj1/holdout.parquet",
        "cd linkdir/proj1 && ml-referee audit",
        f"cd {lay.data} && ml-referee audit --against 'm*/p*/h*'",
        "cd $X && ml-referee audit",
        "cd - && ml-referee audit",
        "cd && ml-referee audit --against .local/share/m*/p*/h*",
        "pushd /tmp && ml-referee audit",
        "CDPATH=/x; cd y && ml-referee audit",
        # subcommands and flags outside the agent set
        "ml-referee seal --evaluator e.py",
        "ml-referee seal --evaluator e.py --holdout-file h.csv",
        "ml-referee claim --predictions p.csv",
        "ml-referee claim --predictions p.csv --out report.md",
        "ml-referee install --harness all",
        "ml-referee",
        "ml-referee bogus",
        "ml-referee --version",
        "ml-referee audit --config .ml-referee.toml",
        "ml-referee audit --config other.toml",
        "ml-referee eval --predictions p.csv --out x",
        "ml-referee seal --help --evaluator e.py",
        "ml-referee bogus --help",
        "ml-referee eval -h --predictions p.csv",
        # wrappers and invocations that are not the installed CLI
        ".venv/bin/ml-referee audit",
        "./ml-referee audit",
        "/usr/bin/ml-referee audit",
        "python ml-referee audit",
        "PATH=/tmp ml-referee audit",
        "XDG_DATA_HOME=/tmp/x ml-referee audit",
        "HOME=/tmp ml-referee audit",
        "ML_REFEREE_X=1 ml-referee audit",
        "uv run --with evil ml-referee audit",
        "uv run --python /tmp/py ml-referee audit",
        "timeout ml-referee audit",
        "timeout -k ml-referee audit",
        "which ml-referee other",
        "command -v ml-referee other",
        "which -a ml-referee",
        "ml-referee audit --predict-cmd x",
        # predict-cmd arguments
        f"ml-referee eval --predict-cmd 'cat {store}/holdout.parquet'",
        "ml-referee eval --predict-cmd 'cat link/holdout.parquet'",
        "ml-referee eval --predict-cmd 'cat .ml-referee.toml'",
        "ml-referee eval --predict-cmd 'python $X {features}'",
        "ml-referee eval --predict-cmd 'python p.py {features} {out} {x}'",
        "ml-referee eval --predict-cmd 'python p.py {a,b}'",
        "ml-referee eval --predict-cmd 'python p.py `id`'",
        "ml-referee eval --predict-cmd 'ml-referee eval'",
        "ml-referee eval --predict-cmd 'bash -c \"cat ~/.local/share\"'",
    ]


@pytest.mark.parametrize("harness", HARNESSES)
def test_mirror_forms_denied(
    lay: Lay, monkeypatch: pytest.MonkeyPatch, harness: str,
) -> None:
    for command in mirror(lay):
        assert not verdict(harness, command, lay, monkeypatch), command


def test_relative_store_links_denied_from_subdirectory(
    lay: Lay, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(lay.ws))
    for command in (
        "ml-referee audit --against ../link/holdout.parquet",
        "ml-referee audit > ../linkfile",
        "ml-referee audit; cat ../linkdir/proj1/evaluator.py",
        "cd .. && ml-referee audit --against linkfile",
    ):
        raw = bash_payload("claude", command, lay.ws / "sub", monkeypatch)
        assert not _evaluate("claude", raw)[1].allow, command


def test_reading_the_config_in_a_compound_command_matches_the_single_form(
    lay: Lay, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert verdict("claude", "cat .ml-referee.toml", lay, monkeypatch)
    assert verdict("claude", "ml-referee audit | cat .ml-referee.toml", lay, monkeypatch)
    assert not verdict("claude", "cat .ml-referee.toml > x", lay, monkeypatch)


def test_every_allowed_form_has_a_denied_counterpart(lay: Lay) -> None:
    # A guard that allows everything would pass the allowed list; keep both lists live.
    assert len(ALLOWED) >= 12
    assert len(mirror(lay)) >= 100


def test_workspace_symlink_into_store_is_followed(
    lay: Lay, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A relative path through a real symlink in the workspace resolves into the store.
    (lay.ws / "evil").symlink_to(lay.root / "proj1")
    assert not verdict("claude", "ml-referee audit --against evil/holdout.parquet", lay, monkeypatch)
    assert not verdict("claude", "ml-referee audit > evil/out", lay, monkeypatch)


def test_cli_still_blocked_when_cwd_is_in_store(
    lay: Lay, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(lay.ws))
    for cwd in (lay.store, lay.root, lay.data):
        raw = bash_payload("claude", "ml-referee audit", cwd, monkeypatch, lay.ws)
        assert not _evaluate("claude", raw)[1].allow


def test_unknown_user_tilde_is_literal(
    lay: Lay, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python 3.14 raises on ``~0.5``; the shell leaves it literal."""
    original = Path.expanduser

    def strict(self: Path) -> Path:
        text = str(self)
        if text.startswith("~") and text != "~" and not text.startswith("~/"):
            msg = "Could not determine home directory."
            raise RuntimeError(msg)
        return original(self)

    monkeypatch.setattr(Path, "expanduser", strict)
    assert verdict("claude", "echo 'ridge CV ~0.5035 vs majority ~0.5002'", lay, monkeypatch)
    assert verdict("claude", "printf '~0.5\\n' >> results.tsv", lay, monkeypatch)
    # ``~`` and ``~/...`` stay fail closed and stay denied for store paths.
    assert not verdict("claude", "cat ~/.local/share/ml-referee/proj1/holdout.parquet", lay, monkeypatch)
    assert not verdict("claude", "ml-referee audit; cat ~/.local/share/ml-referee/proj1/x", lay, monkeypatch)


def test_split_commands_shapes() -> None:
    parsed = split_commands("a x | b y 2>&1; c > f &")
    assert parsed is not None
    assert [c.words for c in parsed] == [("a", "x"), ("b", "y"), ("c",)]
    assert parsed[1].redirects == (("2>&", "1"),)
    assert parsed[2].redirects == ((">", "f"),)
    one = split_commands("echo 'a;b' \"c && d\"")
    assert one is not None
    assert [c.words for c in one] == [("echo", "a;b", "c && d")]
    multi = split_commands("a\nb")
    assert multi is not None
    assert [c.words for c in multi] == [("a",), ("b",)]
    sub = split_commands("(a && b) > f")
    assert sub is not None
    assert sub[-1].redirects == ((">", "f"),)


@pytest.mark.parametrize(
    "line",
    [
        "echo $(id)", "echo `id`", "echo \"$(id)\"", "echo <(id)", "cat <<EOF\nx\nEOF",
        "cat <<< x", "echo 'unterminated", "echo \"unterminated", "echo $'x'",
        "echo $\"x\"", "ls # comment", "a > >(b)", "a >",
    ],
)
def test_split_commands_refuses_unmodelled_syntax(line: str) -> None:
    assert split_commands(line) is None
