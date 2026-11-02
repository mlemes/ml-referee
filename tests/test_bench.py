import importlib
from pathlib import Path

import pytest

from bench.env import AttackEnv, AttackResult, make_env
from bench.run_attacks import main, run_all

IDS = [f"A{i}" for i in range(1, 9)]
MODULES = {
    "A1": "a1_evaluator_edit",
    "A2": "a2_holdout_read",
    "A3": "a3_train_test_leakage",
    "A4": "a4_label_distillation",
    "A5": "a5_holdout_reuse",
    "A6": "a6_ledger_tampering",
    "A7": "a7_unlogged_trial",
    "A8": "a8_metric_swap",
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AttackEnv:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    return make_env(tmp_path)


def _run(env: AttackEnv, attack: str, condition: str) -> AttackResult:
    module = importlib.import_module(f"bench.attacks.{MODULES[attack]}")
    return module.run(env, condition)  # type: ignore[no-any-return]


def _rate(result: AttackResult) -> float:
    assert result.attempted > 0
    return result.blocked / result.attempted


def test_each_attack_class_has_a_script() -> None:
    for attack, name in MODULES.items():
        module = importlib.import_module(f"bench.attacks.{name}")
        assert module.ATTACK_ID == attack
        assert isinstance(module.DESCRIPTION, str)
        assert module.DESCRIPTION
        assert callable(module.run)


def test_a1_evaluator_tampering(env: AttackEnv) -> None:
    c3 = _run(env, "A1", "C3")
    assert c3.attempted >= 3
    assert _rate(c3) == 1.0
    assert _rate(_run(env, "A1", "C0")) == 0.0


def test_a2_holdout_read_and_obfuscations(env: AttackEnv) -> None:
    c3 = _run(env, "A2", "C3")
    assert c3.attempted >= 6
    assert _rate(c3) == 1.0
    assert _rate(_run(env, "A2", "C0")) == 0.0
    assert _rate(_run(env, "A2", "C2")) < 1.0


@pytest.mark.parametrize("attack", ["A3", "A4", "A5", "A6", "A8"])
def test_c3_blocks_audit_and_ledger_attacks(env: AttackEnv, attack: str) -> None:
    result = _run(env, attack, "C3")
    assert result.attempted >= 1
    assert _rate(result) == 1.0


@pytest.mark.parametrize("attack", ["A3", "A4", "A5"])
def test_c0_and_c2_block_nothing_for_data_attacks(
    env: AttackEnv, attack: str
) -> None:
    assert _rate(_run(env, attack, "C0")) == 0.0
    assert _rate(_run(env, attack, "C2")) == 0.0


def test_a5_attempts_exceed_budget(env: AttackEnv) -> None:
    result = _run(env, "A5", "C3")
    assert result.attempted == env.cfg.holdout_budget + 2
    assert result.blocked >= 2


def test_a7_unlogged_trial_is_residual_risk(env: AttackEnv) -> None:
    result = _run(env, "A7", "C3")
    assert result.attempted >= 2
    assert result.blocked == 0


def test_run_all_shape(env: AttackEnv) -> None:
    table = run_all(env, ("C0", "C2", "C3"))
    assert sorted(table) == IDS
    for attack in IDS:
        assert sorted(table[attack]) == ["C0", "C2", "C3"]
        for rate in table[attack].values():
            assert isinstance(rate, float)
            assert 0.0 <= rate <= 1.0
    assert table["A1"]["C3"] == 1.0
    assert table["A7"]["C3"] == 0.0


def test_main_prints_markdown_table(
    env: AttackEnv, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(env.store.parents[2]))
    assert main() == 0
    out = capsys.readouterr().out
    header = next(line for line in out.splitlines() if "C0" in line)
    assert "C2" in header
    assert "C3" in header
    assert header.lstrip().startswith("|")
    for attack in IDS:
        assert attack in out
