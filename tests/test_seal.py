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


def test_ledger_appendable_after_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, _ = _setup(tmp_path, monkeypatch)
    seal(cfg, ev)
    ledger = Ledger(store_dir(cfg) / "ledger.jsonl")
    ledger.append("trial", {"cmd": "x"})
    assert ledger.verify().ok is True
    assert [r.kind for r in ledger.records()] == ["seal", "trial"]


def test_sealed_artifacts_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, _ = _setup(tmp_path, monkeypatch)
    seal(cfg, ev)
    d = store_dir(cfg)
    for name in ("holdout.parquet", "manifest.json", "evaluator.py", "source.csv"):
        assert stat.S_IMODE((d / name).stat().st_mode) == 0o400, name
    for name in ("ledger.jsonl", "head.txt"):
        path = d / name
        if path.exists():
            assert stat.S_IMODE(path.stat().st_mode) & 0o200, name


def test_workspace_train_file_keeps_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, _ = _setup(tmp_path, monkeypatch)
    cfg.train_path.chmod(0o640)
    seal(cfg, ev)
    assert stat.S_IMODE(cfg.train_path.stat().st_mode) == 0o640


def _holdout_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Five rows with new ids, new groups, and a non-sorted order."""
    hold = df.iloc[[7, 3, 150, 0, 99]].copy()
    hold["row_id"] = [1005, 1003, 1001, 1004, 1002]
    hold["g"] = [900, 901, 902, 903, 904]
    return hold.reset_index(drop=True)


def _write(frame: pd.DataFrame, path: Path) -> Path:
    if path.suffix == ".csv":
        frame.to_csv(path, index=False)
    else:
        frame.to_parquet(path, index=False)
    return path


def _assert_refused(cfg: Config, ev: Path, src: Path) -> None:
    before = cfg.train_path.read_bytes()
    with pytest.raises(SealError):
        seal(cfg, ev, src)
    assert not store_dir(cfg).exists()
    assert cfg.train_path.read_bytes() == before


@pytest.mark.parametrize("train_fmt", ["csv", "parquet"])
@pytest.mark.parametrize("hold_fmt", ["csv", "parquet"])
def test_seal_with_holdout_file_keeps_order_and_train(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, train_fmt: str, hold_fmt: str
) -> None:
    cfg, ev, df = _setup(tmp_path, monkeypatch, train_fmt)
    train_bytes = cfg.train_path.read_bytes()
    hold = _holdout_frame(df)
    src = _write(hold[list(reversed(hold.columns))], tmp_path / f"h.{hold_fmt}")
    manifest = seal(cfg, ev, src)
    sealed = pd.read_parquet(store_dir(cfg) / "holdout.parquet")
    assert list(sealed.columns) == list(df.columns)
    assert list(sealed["row_id"]) == [1005, 1003, 1001, 1004, 1002]
    assert sealed.equals(hold)
    assert manifest.n_holdout == 5
    assert load_manifest(cfg) == manifest
    assert verify_manifest(cfg) is True
    assert cfg.train_path.read_bytes() == train_bytes
    assert len(_read(cfg.train_path)) == len(df)
    source = store_dir(cfg) / f"source.{train_fmt}"
    assert source.read_bytes() == train_bytes
    assert stat.S_IMODE(source.stat().st_mode) == 0o400
    assert stat.S_IMODE((store_dir(cfg) / "holdout.parquet").stat().st_mode) == 0o400
    assert (store_dir(cfg) / "evaluator.py").read_text() == EVALUATOR


def test_seal_with_parquet_holdout_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, df = _setup(tmp_path, monkeypatch, "parquet")
    src = _write(_holdout_frame(df), tmp_path / "h.parquet")
    seal(cfg, ev, src)
    assert (store_dir(cfg) / "holdout.parquet").read_bytes() == src.read_bytes()


def test_seal_with_holdout_file_skips_group_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, df = _setup(tmp_path, monkeypatch)
    hold = df.iloc[:3].copy()  # groups also present in train
    src = _write(hold, tmp_path / "h.csv")
    manifest = seal(cfg, ev, src)
    assert manifest.n_holdout == 3
    assert len(_read(cfg.train_path)) == len(df)
    assert _holdout_ids(cfg) == {0, 1, 2}


def test_seal_with_holdout_file_records_one_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, df = _setup(tmp_path, monkeypatch)
    seal(cfg, ev, _write(_holdout_frame(df), tmp_path / "h.csv"))
    records = Ledger(store_dir(cfg) / "ledger.jsonl").records()
    assert [r.kind for r in records] == ["seal"]


def test_seal_with_holdout_sharing_rows_with_train_still_seals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, df = _setup(tmp_path, monkeypatch)
    src = _write(df.iloc[:10].copy(), tmp_path / "h.parquet")
    manifest = seal(cfg, ev, src)
    assert manifest.n_holdout == 10
    assert _holdout_ids(cfg) == set(range(10))
    assert set(_read(cfg.train_path)["row_id"]) >= set(range(10))


@pytest.mark.parametrize("variant", ["missing", "extra", "renamed", "no_label"])
def test_seal_holdout_columns_differ_raises_before_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    cfg, ev, df = _setup(tmp_path, monkeypatch)
    hold = _holdout_frame(df)
    if variant == "missing":
        hold = hold.drop(columns=["x"])
    elif variant == "extra":
        hold["extra"] = 1
    elif variant == "renamed":
        hold = hold.rename(columns={"x": "x2"})
    else:
        hold = hold.drop(columns=["y"])
    _assert_refused(cfg, ev, _write(hold, tmp_path / "h.csv"))


@pytest.mark.parametrize("suffix", [".csv", ".parquet"])
def test_seal_holdout_zero_rows_raises_before_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    cfg, ev, df = _setup(tmp_path, monkeypatch)
    empty = _holdout_frame(df).iloc[:0]
    _assert_refused(cfg, ev, _write(empty, tmp_path / f"h{suffix}"))


@pytest.mark.parametrize("suffix", [".csv", ".parquet"])
def test_seal_holdout_unreadable_raises_before_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    cfg, ev, _ = _setup(tmp_path, monkeypatch)
    bad = tmp_path / f"h{suffix}"
    bad.write_bytes(b"\x00\xff\x00not a table")
    _assert_refused(cfg, ev, bad)


def test_seal_holdout_missing_file_raises_before_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, _ = _setup(tmp_path, monkeypatch)
    _assert_refused(cfg, ev, tmp_path / "absent.csv")


def test_seal_holdout_bad_suffix_raises_before_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, ev, df = _setup(tmp_path, monkeypatch)
    path = tmp_path / "h.txt"
    _holdout_frame(df).to_csv(path, index=False)
    _assert_refused(cfg, ev, path)
