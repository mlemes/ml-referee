import multiprocessing as mp
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from ml_referee import runner
from ml_referee.config import Config, load_config, store_dir
from ml_referee.ledger import Ledger
from ml_referee.runner import (
    EVAL_TIMEOUT_S,
    BudgetExhausted,
    EvaluatorError,
    EvalResult,
    IntegrityError,
    run_holdout_eval,
)
from ml_referee.seal import seal

GOOD = """import json, sys
import pandas as pd
h = pd.read_parquet(sys.argv[1])
p = pd.read_csv(sys.argv[2])
acc = float((h["y"].to_numpy() == p["pred"].to_numpy()).mean())
print(json.dumps({"value": acc, "n": len(h)}))
"""
FIXED = (
    'import json, sys\nprint(json.dumps({"value": 0.87316, "n": %d}))\n'
)


def _setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    evaluator: str = GOOD,
    budget: int = 1,
    reserve: int = 0,
) -> tuple[Config, Path]:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    work = tmp_path / "w"
    (work / "data").mkdir(parents=True)
    rng = np.random.default_rng(3)
    n = 100
    pd.DataFrame(
        {"x": rng.normal(size=n), "y": rng.integers(0, 2, size=n)}
    ).to_csv(work / "data" / "train.csv", index=False)
    (work / "cfg.toml").write_text(
        '[project]\nid = "proj-r"\n\n[data]\ntrain = "data/train.csv"\n'
        'label = "y"\n\n[eval]\ncommand = "python eval.py"\nmetric = "acc"\n'
        "higher_is_better = true\n"
        f"claim_reserve = {reserve}\n\n[trials]\npatterns = []\n\n"
        f"[holdout]\nfraction = 0.25\nseed = 1\nbudget = {budget}\n"
    )
    cfg = load_config(work / "cfg.toml")
    src = work / "ev_src.py"
    src.write_text(evaluator)
    seal(cfg, src)
    hold = pd.read_parquet(store_dir(cfg) / "holdout.parquet")
    preds = work / "preds.csv"
    pd.DataFrame({"pred": hold["y"].to_numpy()}).to_csv(preds, index=False)
    return cfg, preds


def _eval_records(cfg: Config) -> int:
    return Ledger(store_dir(cfg) / "ledger.jsonl").eval_count()


def _writable(path: Path) -> None:
    path.chmod(0o600)


def test_timeout_constant_default() -> None:
    assert EVAL_TIMEOUT_S == 60
    assert issubclass(EvaluatorError, RuntimeError)


def test_first_eval_returns_value_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    hold = pd.read_parquet(store_dir(cfg) / "holdout.parquet")
    result = run_holdout_eval(cfg, preds)
    assert isinstance(result, EvalResult)
    assert result.metric == "acc"
    assert result.value == pytest.approx(1.0)
    assert result.n == len(hold)
    assert _eval_records(cfg) == 1


def test_value_comes_from_evaluator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    hold = pd.read_parquet(store_dir(cfg) / "holdout.parquet")
    flipped = 1 - hold["y"].to_numpy()
    flipped[:3] = hold["y"].to_numpy()[:3]
    pd.DataFrame({"pred": flipped}).to_csv(preds, index=False)
    result = run_holdout_eval(cfg, preds)
    assert result.value == pytest.approx(3 / len(hold))


def test_second_eval_raises_budget_exhausted_and_prints_no_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch, evaluator=FIXED % 25)
    run_holdout_eval(cfg, preds)
    capfd.readouterr()
    with pytest.raises(BudgetExhausted) as info:
        run_holdout_eval(cfg, preds)
    out = capfd.readouterr()
    assert _eval_records(cfg) == 1
    for text in (str(info.value), out.out, out.err):
        assert "87316" not in text
        assert "0.873" not in text


def test_budget_two_allows_two_then_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch, budget=2)
    run_holdout_eval(cfg, preds)
    run_holdout_eval(cfg, preds)
    assert _eval_records(cfg) == 2
    with pytest.raises(BudgetExhausted):
        run_holdout_eval(cfg, preds)
    assert _eval_records(cfg) == 2


def test_tampered_evaluator_raises_integrity_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    ev = store_dir(cfg) / "evaluator.py"
    _writable(ev)
    ev.write_text(FIXED % 25)
    with pytest.raises(IntegrityError):
        run_holdout_eval(cfg, preds)
    assert _eval_records(cfg) == 0


def test_tampered_ledger_raises_integrity_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    ledger = store_dir(cfg) / "ledger.jsonl"
    _writable(ledger)
    ledger.write_text(ledger.read_text().replace("seal", "seaL", 1))
    with pytest.raises(IntegrityError):
        run_holdout_eval(cfg, preds)


def test_evaluator_nonzero_exit_does_not_consume_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "go.marker"
    evaluator = (
        "import os, sys\n"
        f"if not os.path.exists({str(marker)!r}):\n"
        "    sys.exit(3)\n" + GOOD
    )
    cfg, preds = _setup(tmp_path, monkeypatch, evaluator=evaluator)
    with pytest.raises(EvaluatorError):
        run_holdout_eval(cfg, preds)
    assert _eval_records(cfg) == 0
    marker.write_text("ok")
    assert run_holdout_eval(cfg, preds).value == pytest.approx(1.0)
    assert _eval_records(cfg) == 1


def test_missing_ledger_raises_integrity_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    (store_dir(cfg) / "ledger.jsonl").unlink()
    with pytest.raises(IntegrityError):
        run_holdout_eval(cfg, preds)


def test_missing_head_raises_integrity_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    (store_dir(cfg) / "head.txt").unlink()
    with pytest.raises(IntegrityError):
        run_holdout_eval(cfg, preds)


def test_empty_ledger_with_head_raises_integrity_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    ledger = store_dir(cfg) / "ledger.jsonl"
    _writable(ledger)
    ledger.write_text("")
    with pytest.raises(IntegrityError):
        run_holdout_eval(cfg, preds)


def test_missing_store_dir_raises_integrity_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    shutil.rmtree(store_dir(cfg))
    with pytest.raises(IntegrityError):
        run_holdout_eval(cfg, preds)


def test_first_record_not_seal_raises_integrity_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    directory = store_dir(cfg)
    for name in ("ledger.jsonl", "head.txt"):
        (directory / name).unlink()
    Ledger(directory / "ledger.jsonl").append("trial", {"a": 1})
    with pytest.raises(IntegrityError):
        run_holdout_eval(cfg, preds)
    assert _eval_records(cfg) == 0


def test_predictions_symlink_to_holdout_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    link = tmp_path / "link.csv"
    link.symlink_to(store_dir(cfg) / "holdout.parquet")
    with pytest.raises((EvaluatorError, IntegrityError)):
        run_holdout_eval(cfg, link)
    assert _eval_records(cfg) == 0


def test_predictions_missing_raises_evaluator_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = _setup(tmp_path, monkeypatch)
    with pytest.raises(EvaluatorError):
        run_holdout_eval(cfg, tmp_path / "nope.csv")
    assert _eval_records(cfg) == 0


def test_timeout_kills_grandchild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pidfile = tmp_path / "grandchild.pid"
    evaluator = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(30)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(30)\n"
    )
    cfg, preds = _setup(tmp_path, monkeypatch, evaluator=evaluator)
    monkeypatch.setattr(runner, "EVAL_TIMEOUT_S", 1)
    start = time.monotonic()
    with pytest.raises(EvaluatorError):
        run_holdout_eval(cfg, preds)
    assert time.monotonic() - start < 6
    pid = int(pidfile.read_text())
    dead = False
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            dead = True
            break
        time.sleep(0.1)
    assert dead
    assert _eval_records(cfg) == 0


_LEAK = (
    "import sys\n"
    "sys.stdout.write('SECRET-0.9137\\n'); sys.stderr.write('SECRET-0.9137\\n')\n"
)


@pytest.mark.parametrize(
    "kind", ["nonzero", "bad_json", "timeout", "n_mismatch"]
)
def test_failure_paths_do_not_leak_evaluator_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    tail = {
        "nonzero": "sys.exit(3)\n",
        "bad_json": "",
        "timeout": "import time\ntime.sleep(30)\n",
        "n_mismatch": 'print(\'{"value": 0.5, "n": 7}\')\n',
    }[kind]
    cfg, preds = _setup(tmp_path, monkeypatch, evaluator=_LEAK + tail)
    if kind == "timeout":
        monkeypatch.setattr(runner, "EVAL_TIMEOUT_S", 1)
    capfd.readouterr()
    with pytest.raises((EvaluatorError, IntegrityError)) as info:
        run_holdout_eval(cfg, preds)
    exc = info.value
    out = capfd.readouterr()
    for text in (
        str(exc),
        repr(exc.__cause__),
        repr(exc.__context__),
        out.out,
        out.err,
    ):
        assert "SECRET-0.9137" not in text
        assert "0.9137" not in text


def test_eval_lock_mode_is_0600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    run_holdout_eval(cfg, preds)
    mode = (store_dir(cfg) / "eval.lock").stat().st_mode & 0o777
    assert mode == 0o600


@pytest.mark.parametrize(
    "body",
    [
        "print('not json')\n",
        "print('{\"value\": NaN, \"n\": 25}')\n",
        "print('{\"n\": 25}')\n",
    ],
)
def test_bad_evaluator_output_raises_evaluator_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch, evaluator=body)
    with pytest.raises(EvaluatorError):
        run_holdout_eval(cfg, preds)
    assert _eval_records(cfg) == 0


def test_evaluator_timeout_raises_evaluator_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(
        tmp_path, monkeypatch, evaluator="import time\ntime.sleep(30)\n"
    )
    monkeypatch.setattr(runner, "EVAL_TIMEOUT_S", 1)
    with pytest.raises(EvaluatorError):
        run_holdout_eval(cfg, preds)
    assert _eval_records(cfg) == 0


def test_evaluator_n_mismatch_raises_integrity_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch, evaluator=FIXED % 7)
    with pytest.raises(IntegrityError):
        run_holdout_eval(cfg, preds)
    assert _eval_records(cfg) == 0


def _worker(cfg: Config, preds: Path, barrier: Any, queue: Any) -> None:
    barrier.wait()
    try:
        run_holdout_eval(cfg, preds)
        queue.put("ok")
    except BudgetExhausted:
        queue.put("budget")
    except Exception as exc:  # noqa: BLE001
        queue.put(f"error:{type(exc).__name__}")


def test_concurrent_evals_respect_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    procs = [
        ctx.Process(target=_worker, args=(cfg, preds, barrier, queue))
        for _ in range(2)
    ]
    for proc in procs:
        proc.start()
    results = sorted(queue.get(timeout=60) for _ in procs)
    for proc in procs:
        proc.join(timeout=60)
    assert results == ["budget", "ok"]
    assert _eval_records(cfg) == 1
    assert os.environ["XDG_DATA_HOME"]
    assert re.fullmatch(r"[0-9a-f]{64}", Ledger(store_dir(cfg) / "ledger.jsonl").records()[-1].hash)
