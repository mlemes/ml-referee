import os
import stat
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml_referee.config import Config, load_config, store_dir
from ml_referee.ledger import Ledger
from ml_referee.seal import SealError, load_manifest, seal, verify_manifest

N_GROUPS = 40
PER_GROUP = 5
EVALUATOR = "print('score')\n"


def _frame() -> pd.DataFrame:
    rng = np.random.default_rng(1)
    n = N_GROUPS * PER_GROUP
    return pd.DataFrame(
        {
            "row_id": np.arange(n),
            "g": np.repeat(np.arange(N_GROUPS), PER_GROUP),
            "x": rng.normal(size=n),
            "y": rng.integers(0, 2, size=n),
        }
    )


def _setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fmt: str = "csv",
    seed: int = 7,
    group: bool = True,
    name: str = "w",
) -> tuple[Config, Path, pd.DataFrame]:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    work = tmp_path / name
    (work / "data").mkdir(parents=True)
    df = _frame()
    train = work / "data" / f"train.{fmt}"
    if fmt == "csv":
        df.to_csv(train, index=False)
    else:
        df.to_parquet(train, index=False)
    group_line = 'group = "g"\n' if group else ""
    toml = work / "cfg.toml"
    toml.write_text(
        f'[project]\nid = "proj-{name}"\n\n[data]\ntrain = "data/train.{fmt}"\n'
        f'label = "y"\n{group_line}\n[eval]\ncommand = "python eval.py"\n'
        'metric = "auc"\nhigher_is_better = true\n\n[trials]\npatterns = []\n\n'
        f"[holdout]\nfraction = 0.25\nseed = {seed}\n"
    )
    ev = work / "evaluator_src.py"
    ev.write_text(EVALUATOR)
    return load_config(toml), ev, df


def _read(path: Path) -> pd.DataFrame:
    if path.suffix == ".csv":
        return pd.read_csv(path)
    return pd.read_parquet(path)


def _holdout_ids(cfg: Config) -> set[int]:
    return set(pd.read_parquet(store_dir(cfg) / "holdout.parquet")["row_id"])


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_seal_removes_holdout_from_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fmt: str
) -> None:
    cfg, ev, df = _setup(tmp_path, monkeypatch, fmt)
    manifest = seal(cfg, ev)
    train_ids = set(_read(cfg.train_path)["row_id"])
    hold_ids = _holdout_ids(cfg)
    assert hold_ids
    assert train_ids
    assert train_ids.isdisjoint(hold_ids)
    assert train_ids | hold_ids == set(df["row_id"])
    assert manifest.n_holdout == len(hold_ids)
    assert manifest.seed == 7
    assert manifest.metric == "auc"
    assert manifest.higher_is_better is True
    assert (store_dir(cfg) / "evaluator.py").read_text() == EVALUATOR
    assert load_manifest(cfg) == manifest
    assert verify_manifest(cfg) is True


def test_seal_is_reproducible_for_same_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg1, ev1, _ = _setup(tmp_path, monkeypatch, name="a")
    cfg2, ev2, _ = _setup(tmp_path, monkeypatch, name="b")
    m1 = seal(cfg1, ev1)
    m2 = seal(cfg2, ev2)
    assert _holdout_ids(cfg1) == _holdout_ids(cfg2)
    assert m1.holdout_sha256 == m2.holdout_sha256


def test_seal_split_depends_on_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg1, ev1, _ = _setup(tmp_path, monkeypatch, seed=1, name="a")
    cfg2, ev2, _ = _setup(tmp_path, monkeypatch, seed=2, name="b")
    seal(cfg1, ev1)
    seal(cfg2, ev2)
    assert _holdout_ids(cfg1) != _holdout_ids(cfg2)


def test_seal_groups_never_straddle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, _ = _setup(tmp_path, monkeypatch)
    seal(cfg, ev)
    train_groups = set(_read(cfg.train_path)["g"])
    hold_groups = set(pd.read_parquet(store_dir(cfg) / "holdout.parquet")["g"])
    assert train_groups
    assert hold_groups
    assert train_groups.isdisjoint(hold_groups)


def test_seal_without_group_splits_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, df = _setup(tmp_path, monkeypatch, group=False)
    seal(cfg, ev)
    hold = _holdout_ids(cfg)
    assert len(hold) == round(0.25 * len(df))
    assert hold.isdisjoint(set(_read(cfg.train_path)["row_id"]))


def test_seal_refuses_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, _ = _setup(tmp_path, monkeypatch)
    seal(cfg, ev)
    after_first = _read(cfg.train_path)
    with pytest.raises(SealError):
        seal(cfg, ev)
    assert _read(cfg.train_path).equals(after_first)


def test_seal_store_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, _ = _setup(tmp_path, monkeypatch)
    seal(cfg, ev)
    d = store_dir(cfg)
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    for name in ("holdout.parquet", "evaluator.py"):
        assert stat.S_IMODE((d / name).stat().st_mode) == 0o400


def test_verify_manifest_detects_evaluator_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, _ = _setup(tmp_path, monkeypatch)
    seal(cfg, ev)
    assert verify_manifest(cfg) is True
    sealed = store_dir(cfg) / "evaluator.py"
    sealed.chmod(0o600)
    sealed.write_text("print('cheat')\n")
    assert verify_manifest(cfg) is False


def test_verify_manifest_detects_holdout_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, _ = _setup(tmp_path, monkeypatch)
    seal(cfg, ev)
    sealed = store_dir(cfg) / "holdout.parquet"
    sealed.chmod(0o600)
    with sealed.open("ab") as stream:
        stream.write(b"x")
    assert verify_manifest(cfg) is False


def test_seal_appends_seal_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, _ = _setup(tmp_path, monkeypatch)
    seal(cfg, ev)
    records = Ledger(store_dir(cfg) / "ledger.jsonl").records()
    assert [r.kind for r in records] == ["seal"]
    assert os.path.exists(store_dir(cfg) / "ledger.jsonl")
