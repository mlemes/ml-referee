import os
from pathlib import Path

import pytest

from ml_referee.config import load_config, store_dir
from ml_referee.policy import Decision, Policy, build_policy, decide

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
    assert str(policy.store) in d.reason


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
    "cmd", ["python train.py", "pytest -q", "ml-referee eval predictions.csv"],
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


def fill(text: str, policy: Policy) -> str:
    share = policy.store.parent.parent
    return (
        text.replace("<store>", str(policy.store))
        .replace("<share>", str(share))
        .replace("<home>", str(share.parent.parent))
    )


HOME_STORE = "~/.local/share/ml-referee/proj1"
BYPASS_CMDS = [
    # (1) variable-built path
    "X=ml-referee; cat ~/.local/share/$X/proj1/holdout.parquet",
    "X=ml-referee; cat <share>/$X/proj1/holdout.parquet",
    # (2) cd chain
    "cd ~/.local; cd share; cd ml-referee; cd proj1; cat holdout.parquet",
    "cd <share>; cd ml-referee; cd proj1; cat holdout.parquet",
    # (3) quoting splits the name
    'dd if=~/.local/share/ml-"referee"/proj1/holdout.parquet',
    'dd if=<share>/ml-"referee"/proj1/holdout.parquet',
    # (4) env --chdir
    'env --chdir=~/.local/share/ml-"referee"/proj1 cat holdout.parquet',
    "env --chdir=<store> cat holdout.parquet",
    # (5) brace expansion
    "cat ~/.local/share/ml-{referee,x}/proj1/holdout.parquet",
    "cat <share>/ml-{referee,x}/proj1/holdout.parquet",
    # (6) string concatenation in python
    "python -c \"open('/home/u/.local/share/ml-' + 'referee/proj1/holdout.parquet')\"",
    "python -c \"open('<share>/ml-' + 'referee/proj1/holdout.parquet')\"",
    # (7) process substitution
    'cat <(~/.local/share/ml-"referee"/proj1/evaluator.py)',
    'cat <(<share>/ml-"referee"/proj1/evaluator.py)',
    # (8) here-doc
    "sh << 'EOF'\ncd ~/.local; cd share; cd ml-referee; cd proj1; "
    "cat holdout.parquet\nEOF",
    "sh << 'EOF'\ncd <share>; cd ml-referee; cd proj1; cat holdout.parquet\nEOF",
    # (9) copy the parent, then read the copy
    "cp -r ~/.local/share ./leak && cat leak/ml-referee/proj1/holdout.parquet",
    "cp -r <share> ./leak && cat leak/ml-referee/proj1/holdout.parquet",
    # (10) symlink to the parent
    'ln -s ~/.local/share s && cat s/ml-"referee"/proj1/holdout.parquet',
    'ln -s <share> s && cat s/ml-"referee"/proj1/holdout.parquet',
    # (11) two variables
    "A=ml; B=referee; cat ~/.local/share/$A-$B/proj1/holdout.parquet",
    "A=ml; B=referee; cat <share>/$A-$B/proj1/holdout.parquet",
    # (12) custom store parent
    "cp -r <share> ./leak",
    "cp -r <share>/.. ./leak",
    # (13) config protection through Bash
    "rm .ml-referee.toml",
    "sed -i s/budget/9/ .ml-referee.toml",
    "echo x > .ml-referee.toml",
    "mv .ml-referee.toml /tmp/x",
    # (16) the CLI exemption must not hide store arguments
    "./ml-referee <store>/holdout.parquet",
    "ml-referee eval <store>/holdout.parquet",
    f"ml-referee eval {HOME_STORE}/holdout.parquet",
]


@pytest.mark.parametrize("cmd", BYPASS_CMDS)
def test_bash_bypass_denied(cmd: str, policy: Policy) -> None:
    d = decide(bash(fill(cmd, policy), policy), policy)
    assert d.allow is False
    assert d.reason


def _event(name: str, inputs: dict[str, object], policy: Policy) -> dict[str, object]:
    return {"tool_name": name, "tool_input": inputs, "cwd": str(policy.workspace)}


def test_write_content_with_store_path_denied(policy: Policy) -> None:
    ev = _event(
        "Write",
        {"file_path": "notes.py", "content": f"P = '{policy.store}/holdout.parquet'"},
        policy,
    )
    assert decide(ev, policy).allow is False


def test_edit_new_string_with_store_path_denied(policy: Policy) -> None:
    ev = _event(
        "Edit",
        {
            "file_path": "train.py",
            "old_string": "print(1)",
            "new_string": f"open('{policy.store}/holdout.parquet')",
        },
        policy,
    )
    assert decide(ev, policy).allow is False


@pytest.mark.parametrize(
    ("name", "key", "value"),
    [
        ("Grep", "path", "~/.local/share"),
        ("Glob", "pattern", "<share>/**"),
        ("Read", "file_path", "~/.local/share/ml-referee"),
        ("Grep", "path", "~/.local/share/ml-referee"),
    ],
)
def test_search_and_read_tools_on_store_parent_denied(
    name: str, key: str, value: str, policy: Policy,
) -> None:
    ev = tool(name, key, fill(value, policy), policy)
    d = decide(ev, policy)
    assert d.allow is False
    assert d.reason


@pytest.mark.parametrize(
    "cmd",
    [
        "echo $PATH",
        "echo $?",
        'for f in *.py; do python "$f"; done',
        "awk '{print $1}' train.csv",
        "git status",
        "python train.py --lr 3e-4",
        "cat .ml-referee.toml",
        "python holdout_eval.py",
        "ml-referee eval predictions.csv",
    ],
)
def test_benign_commands_still_allowed(cmd: str, policy: Policy) -> None:
    assert decide(bash(cmd, policy), policy).allow is True


def test_write_benign_content_allowed(policy: Policy) -> None:
    ev = _event(
        "Write",
        {"file_path": "train.py", "content": "holdout_score = model.score(X)"},
        policy,
    )
    assert decide(ev, policy).allow is True


def test_grep_path_three_levels_above_store_denied(policy: Policy) -> None:
    ev = tool("Grep", "path", str(policy.store.parent.parent.parent), policy)
    d = decide(ev, policy)
    assert d.allow is False
    assert d.reason


EXTRA_DENIED = [
    "cp -r ~/.local ./leak",
    "find ~/.local -name holdout.parquet -exec cat {} +",
    "ln -s ~/.local s && cat s/*/*/*/holdout.parquet",
    "rm .ml*",
    "sed -i s/budget/9/ .ml*",
    "mv .ml-r* /tmp/x",
    "dd if=~/.l*/share/ml-r*/proj1/holdout.parquet",
]


@pytest.mark.parametrize("cmd", EXTRA_DENIED)
def test_bash_extra_bypass_denied(cmd: str, policy: Policy) -> None:
    d = decide(bash(cmd, policy), policy)
    assert d.allow is False
    assert d.reason


def test_bash_copy_of_store_ancestors_denied(policy: Policy) -> None:
    ws = policy.workspace
    ancestors = [
        p
        for p in policy.store.parents
        if str(p) != "/" and not (p == ws or p in ws.parents)
    ]
    assert ancestors
    for p in ancestors:
        d = decide(bash(f"cp -r {p} ./leak", policy), policy)
        assert d.allow is False, str(p)


@pytest.mark.parametrize(
    "cmd", ["ls .ml*", "cat .ml-referee.toml", "rm build/*.pyc"],
)
def test_config_glob_benign_allowed(cmd: str, policy: Policy) -> None:
    assert decide(bash(cmd, policy), policy).allow is True


# --- symlink handling ---


def _ws_file(policy: Policy, name: str, text: str = "a,b\n1,2\n") -> Path:
    p = policy.workspace / name
    p.write_text(text)
    return p


def _link(policy: Policy, name: str, target: Path) -> Path:
    p = policy.workspace / name
    os.symlink(target, p)
    return p


@pytest.mark.parametrize(
    "cmd", ["cat link", "cat ./link", "head -c 10 link"],
)
def test_bash_symlink_to_store_file_denied(cmd: str, policy: Policy) -> None:
    _link(policy, "link", policy.store / "holdout.parquet")
    assert decide(bash(cmd, policy), policy).allow is False


@pytest.mark.parametrize(
    ("name", "key"), [("Read", "file_path"), ("Grep", "path")],
)
def test_tools_symlink_to_store_file_denied(
    name: str, key: str, policy: Policy,
) -> None:
    link = _link(policy, "link", policy.store / "holdout.parquet")
    assert decide(tool(name, key, str(link), policy), policy).allow is False


def test_glob_symlink_to_store_file_denied(policy: Policy) -> None:
    link = _link(policy, "link", policy.store / "holdout.parquet")
    assert decide(tool("Glob", "pattern", str(link), policy), policy).allow is False


@pytest.mark.parametrize("cmd", ["ls link/", "cat link/holdout.parquet"])
def test_bash_directory_symlink_to_store_denied(cmd: str, policy: Policy) -> None:
    _link(policy, "link", policy.store)
    assert decide(bash(cmd, policy), policy).allow is False


@pytest.mark.parametrize("name", ["Write", "Edit"])
def test_write_edit_symlink_to_store_denied(name: str, policy: Policy) -> None:
    link = _link(policy, "link", policy.store / "evaluator.py")
    assert decide(tool(name, "file_path", str(link), policy), policy).allow is False


def test_nested_symlink_chain_denied(policy: Policy) -> None:
    link = _link(policy, "link", policy.store / "holdout.parquet")
    _link(policy, "link2", link)
    assert decide(bash("cat link2", policy), policy).allow is False


def test_symlink_to_workspace_file_allowed(policy: Policy) -> None:
    train = _ws_file(policy, "train.csv")
    _link(policy, "okay_link", train)
    assert decide(bash("cat okay_link", policy), policy).allow is True


def test_plain_cat_workspace_file_allowed(policy: Policy) -> None:
    _ws_file(policy, "train.csv")
    assert decide(bash("cat train.csv", policy), policy).allow is True


def test_dangling_symlink_does_not_crash(policy: Policy) -> None:
    _link(policy, "dangling", policy.workspace / "nonexistent")
    assert isinstance(decide(bash("cat dangling", policy), policy), Decision)


@pytest.fixture
def demo_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Policy:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    ws = tmp_path / "ml-referee-demo"
    ws.mkdir()
    (ws / ".ml-referee.toml").write_text(TOML)
    (ws / "train.py").write_text("print(1)\n")
    (ws / "train.csv").write_text("a,y\n1,0\n")
    cfg = load_config(ws / ".ml-referee.toml")
    store = store_dir(cfg)
    store.mkdir(parents=True)
    (store / "holdout.parquet").write_text("x")
    return build_policy(cfg, ws)


def test_demo_workspace_path_contains_name(demo_policy: Policy) -> None:
    assert "ml-referee" in str(demo_policy.workspace)


@pytest.mark.parametrize(
    "cmd",
    [
        "cat {ws}/train.csv",
        "python {ws}/train.py",
        "ls {ws}",
    ],
)
def test_bash_in_ml_referee_named_workspace_allowed(
    cmd: str, demo_policy: Policy
) -> None:
    ev = bash(cmd.format(ws=demo_policy.workspace), demo_policy)
    assert decide(ev, demo_policy).allow is True
    sub = demo_policy.workspace / "sub"
    sub.mkdir()
    ev["cwd"] = str(sub)
    assert decide(ev, demo_policy).allow is True


@pytest.mark.parametrize("name", ["Read", "Write", "Edit", "Grep", "Glob"])
def test_file_tools_in_ml_referee_named_workspace_allowed(
    name: str, demo_policy: Policy
) -> None:
    key = {"Grep": "path", "Glob": "pattern"}.get(name, "file_path")
    value = str(demo_policy.workspace / "train.py")
    if name == "Grep":
        value = str(demo_policy.workspace)
    ev = tool(name, key, value, demo_policy)
    assert decide(ev, demo_policy).allow is True


@pytest.mark.parametrize(
    "cmd",
    [
        "cat {store}/holdout.parquet",
        "X=ml-referee; cat ~/.local/share/$X/proj1/holdout.parquet",
    ],
)
def test_ml_referee_named_workspace_still_denies_store(
    cmd: str, demo_policy: Policy
) -> None:
    ev = bash(cmd.format(store=demo_policy.store), demo_policy)
    assert decide(ev, demo_policy).allow is False


@pytest.mark.parametrize(
    "content", ["# don't overfit\nx = 1", '"""unterminated']
)
def test_write_content_with_unmatched_quote_allowed(
    content: str, policy: Policy
) -> None:
    ev = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(policy.workspace / "train.py"),
            "content": content,
        },
        "cwd": str(policy.workspace),
    }
    assert decide(ev, policy).allow is True


def test_edit_new_string_with_apostrophe_allowed(policy: Policy) -> None:
    ev = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(policy.workspace / "train.py"),
            "old_string": "print(1)",
            "new_string": "print(\"don't\")",
        },
        "cwd": str(policy.workspace),
    }
    assert decide(ev, policy).allow is True


def test_write_content_with_store_path_denied(policy: Policy) -> None:
    ev = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(policy.workspace / "train.py"),
            "content": f"open('{policy.store}/holdout.parquet')\n",
        },
        "cwd": str(policy.workspace),
    }
    assert decide(ev, policy).allow is False


def test_bash_unmatched_quote_returns_decision(policy: Policy) -> None:
    assert isinstance(decide(bash("echo don't", policy), policy), Decision)


def test_bash_comment_with_apostrophe_allowed(policy: Policy) -> None:
    ev = bash("# it's fine\npython train.py", policy)
    assert decide(ev, policy).allow is True


@pytest.mark.parametrize("cmd", ['echo "/* c */"', 'echo "/* comment */"', "ls /*"])
def test_slash_star_token_not_store_ancestor_hit(cmd: str, policy: Policy) -> None:
    assert decide(bash(cmd, policy), policy).allow is True


def _mk_policy(ws: Path) -> Policy:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / ".ml-referee.toml").write_text(TOML)
    cfg = load_config(ws / ".ml-referee.toml")
    store = store_dir(cfg)
    store.mkdir(parents=True)
    (store / "holdout.parquet").write_text("x")
    return build_policy(cfg, ws)


def test_workspace_ancestor_of_store_still_denies_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ml-referee-ws"
    monkeypatch.setenv("XDG_DATA_HOME", str(ws / "xdg"))
    pol = _mk_policy(ws)
    assert pol.store.is_relative_to(pol.workspace)
    for cmd in (
        f"cat {pol.store}/holdout.parquet",
        f"cat {ws}/x {pol.store}/holdout.parquet",
        f"cd {pol.store} && ls",
    ):
        assert decide(bash(cmd, pol), pol).allow is False, cmd


def test_cwd_store_parent_denied(
    demo_policy: Policy,
) -> None:
    ev = bash(f"cat {demo_policy.store}/holdout.parquet", demo_policy)
    ev["cwd"] = str(demo_policy.store.parent)
    assert decide(ev, demo_policy).allow is False
    ev = bash(f"ls {demo_policy.store.parent}", demo_policy)
    ev["cwd"] = str(demo_policy.store.parent)
    assert decide(ev, demo_policy).allow is False


def test_quote_trick_next_to_workspace_path_denied(demo_policy: Policy) -> None:
    cmd = (
        f"cat {demo_policy.workspace}/x "
        '~/.local/share/ml-"referee"/proj1/holdout.parquet'
    )
    assert decide(bash(cmd, demo_policy), demo_policy).allow is False


# --- cwd above the store, prefix workspaces, content concatenations ---


def _cwd_event(cmd: str, cwd: Path) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(cwd)}


@pytest.mark.parametrize(
    "cmd",
    [
        "ls",
        "find . -name holdout.parquet -exec cat {} +",
        "python -c \"print(open('proj1/holdout.parquet').read())\"",
    ],
)
def test_bash_cwd_store_parent_denied(cmd: str, policy: Policy) -> None:
    d = decide(_cwd_event(cmd, policy.store.parent), policy)
    assert d.allow is False
    assert d.reason


def test_bash_cwd_store_denied(policy: Policy) -> None:
    d = decide(_cwd_event("cat holdout.parquet", policy.store), policy)
    assert d.allow is False


def test_bash_cwd_store_grandparent_denied(policy: Policy) -> None:
    d = decide(_cwd_event("ls", policy.store.parent.parent), policy)
    assert d.allow is False


@pytest.fixture
def prefix_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Policy:
    ws = tmp_path / "ml-referee"
    ws.mkdir()
    (ws / ".ml-referee.toml").write_text(TOML)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "ml-referee-xdg"))
    cfg = load_config(ws / ".ml-referee.toml")
    store = store_dir(cfg)
    store.mkdir(parents=True)
    for name in ("holdout.parquet", "evaluator.py", "manifest.json"):
        (store / name).write_text("x")
    return build_policy(cfg, ws)


@pytest.mark.parametrize(
    "cmd",
    [
        "cp -r <share> ./leak",
        "A=ml; B=referee; cat <share>/$A-$B/proj1/holdout.parquet",
    ],
)
def test_workspace_string_prefix_of_share_denied(
    cmd: str, prefix_policy: Policy,
) -> None:
    d = decide(bash(fill(cmd, prefix_policy), prefix_policy), prefix_policy)
    assert d.allow is False
    assert d.reason


def test_workspace_with_ml_referee_name_allows_ordinary_commands(
    prefix_policy: Policy,
) -> None:
    ws = prefix_policy.workspace
    for cmd in (f"cat {ws}/train.csv", f"python {ws}/train.py", f"ls {ws}"):
        assert decide(bash(cmd, prefix_policy), prefix_policy).allow is True, cmd


def test_write_content_with_store_path_still_denied(policy: Policy) -> None:
    ev = _event(
        "Write",
        {"file_path": "t.py", "content": f"p = '{policy.store}'"},
        policy,
    )
    assert decide(ev, policy).allow is False
