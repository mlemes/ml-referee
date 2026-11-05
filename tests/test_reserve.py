"""Claim reserve: agent evals cannot spend the budget slot kept for the claim."""

import multiprocessing as mp
from pathlib import Path
from typing import Any

import pytest

from ml_referee.claim import claim
from ml_referee.config import Config, ConfigError, load_config, store_dir
from ml_referee.ledger import Ledger, Record
from ml_referee.runner import (
    BudgetExhausted,
    run_holdout_eval,
    run_predict_eval,
)

from tests.test_runner import _setup


def _records(cfg: Config) -> list[Record]:
    return [
        r
        for r in Ledger(store_dir(cfg) / "ledger.jsonl").records()
        if r.kind == "eval"
    ]


def test_agent_eval_refused_when_remaining_equals_reserve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch, budget=2, reserve=1)
    run_holdout_eval(cfg, preds)
    with pytest.raises(BudgetExhausted, match="reserved for the final claim"):
        run_holdout_eval(cfg, preds)
    assert len(_records(cfg)) == 1


def test_predict_cmd_path_also_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch, budget=2, reserve=1)
    run_holdout_eval(cfg, preds)
    with pytest.raises(BudgetExhausted, match="reserved for the final claim"):
        run_predict_eval(cfg, "echo {features} {out}")
    assert len(_records(cfg)) == 1


def test_claim_spends_the_reserve_and_ledger_records_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch, budget=2, reserve=1)
    run_holdout_eval(cfg, preds)
    claim(cfg, preds)
    assert [r.payload["role"] for r in _records(cfg)] == ["agent", "claim"]
    assert Ledger(store_dir(cfg) / "ledger.jsonl").verify().ok
    with pytest.raises(BudgetExhausted, match="exhausted"):
        claim(cfg, preds)


def test_claim_refused_when_fully_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch, budget=1, reserve=0)
    run_holdout_eval(cfg, preds)
    with pytest.raises(BudgetExhausted):
        claim(cfg, preds)


def test_reserve_zero_keeps_old_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch, budget=2, reserve=0)
    run_holdout_eval(cfg, preds)
    run_holdout_eval(cfg, preds)
    assert len(_records(cfg)) == 2


def _worker(cfg: Config, preds: Path, barrier: Any, queue: Any) -> None:
    barrier.wait()
    try:
        run_holdout_eval(cfg, preds)
        queue.put("ok")
    except BudgetExhausted:
        queue.put("budget")
    except Exception as exc:  # noqa: BLE001
        queue.put(f"error:{type(exc).__name__}")


def test_concurrent_agents_cannot_eat_the_reserve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch, budget=3, reserve=1)
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(5)
    queue = ctx.Queue()
    procs = [
        ctx.Process(target=_worker, args=(cfg, preds, barrier, queue))
        for _ in range(5)
    ]
    for proc in procs:
        proc.start()
    results = sorted(queue.get(timeout=120) for _ in procs)
    for proc in procs:
        proc.join(timeout=60)
    assert results == ["budget"] * 3 + ["ok"] * 2
    assert len(_records(cfg)) == 2
    assert Ledger(store_dir(cfg) / "ledger.jsonl").verify().ok
    claim(cfg, preds)
    assert len(_records(cfg)) == 3


def test_unknown_role_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch, budget=2, reserve=1)
    with pytest.raises(ValueError, match="role"):
        run_holdout_eval(cfg, preds, role="root")


def _cfg_text(budget: int, line: str) -> str:
    return (
        '[project]\nid = "p"\n[data]\ntrain = "t.csv"\nlabel = "y"\n'
        '[eval]\ncommand = "x"\nmetric = "m"\nhigher_is_better = true\n'
        f"{line}\n[trials]\npatterns = []\n[holdout]\nbudget = {budget}\n"
    )


@pytest.mark.parametrize(
    ("budget", "line", "expected"),
    [
        (1, "", 0),
        (2, "", 1),
        (5, "", 1),
        (5, "claim_reserve = 2", 2),
        (3, "claim_reserve = 0", 0),
    ],
)
def test_config_reserve_default(
    tmp_path: Path, budget: int, line: str, expected: int
) -> None:
    (tmp_path / "c.toml").write_text(_cfg_text(budget, line))
    assert load_config(tmp_path / "c.toml").claim_reserve == expected


@pytest.mark.parametrize(("budget", "reserve"), [(1, 1), (2, 2), (2, -1)])
def test_config_reserve_out_of_range(
    tmp_path: Path, budget: int, reserve: int
) -> None:
    (tmp_path / "c.toml").write_text(
        _cfg_text(budget, f"claim_reserve = {reserve}")
    )
    with pytest.raises(ConfigError, match="claim_reserve"):
        load_config(tmp_path / "c.toml")


@pytest.mark.parametrize(
    ("line", "expected"),
    [("", 20), ("[audit]\ntrial_threshold = 5", 5)],
)
def test_config_trial_threshold(tmp_path: Path, line: str, expected: int) -> None:
    (tmp_path / "c.toml").write_text(_cfg_text(2, "") + line + "\n")
    assert load_config(tmp_path / "c.toml").trial_threshold == expected


def test_config_trial_threshold_must_be_positive(tmp_path: Path) -> None:
    (tmp_path / "c.toml").write_text(
        _cfg_text(2, "") + "[audit]\ntrial_threshold = 0\n"
    )
    with pytest.raises(ConfigError, match="trial_threshold"):
        load_config(tmp_path / "c.toml")
