"""Seal the holdout split and the evaluator outside the workspace."""

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ml_referee.config import Config, store_dir
from ml_referee.ledger import Ledger


class SealError(RuntimeError):
    """Raised when sealing is refused or fails."""


@dataclass(frozen=True)
class Manifest:
    """Hashes and settings recorded at seal time."""

    holdout_sha256: str
    evaluator_sha256: str
    seed: int
    metric: str
    higher_is_better: bool
    n_holdout: int


def seal(
    cfg: Config, evaluator_src: Path, holdout_src: Path | None = None,
) -> Manifest:
    """Move the holdout rows and the evaluator into the sealed store."""
    directory = store_dir(cfg)
    if directory.exists():
        msg = f"Store already exists: {directory}"
        raise SealError(msg)
    if cfg.train_path.suffix not in {".csv", ".parquet"}:
        msg = "Training data must be CSV or Parquet"
        raise SealError(msg)
    frame = (
        pd.read_csv(cfg.train_path)
        if cfg.train_path.suffix == ".csv"
        else pd.read_parquet(cfg.train_path)
    )
    if cfg.label not in frame or (cfg.group is not None and cfg.group not in frame):
        msg = "Training data is missing the label or group column"
        raise SealError(msg)
    evaluator = evaluator_src.read_bytes()
    external = (
        _load_external_holdout(holdout_src, list(frame.columns))
        if holdout_src is not None
        else None
    )
    mask = _split_mask(cfg, frame) if external is None else None
    directory.parent.mkdir(parents=True, exist_ok=True)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError as exc:
        msg = f"Store already exists: {directory}"
        raise SealError(msg) from exc
    directory.chmod(0o700)
    source_mode = stat.S_IMODE(cfg.train_path.stat().st_mode)
    source = directory / f"source{cfg.train_path.suffix}"
    try:
        (shutil.move if mask is not None else shutil.copy2)(cfg.train_path, source)
        source.chmod(0o400)
        n_holdout = _write_holdout(
            frame, mask, external, holdout_src, directory / "holdout.parquet",
        )
        (directory / "evaluator.py").write_bytes(evaluator)
        manifest = Manifest(
            holdout_sha256=_sha256(directory / "holdout.parquet"),
            evaluator_sha256=_sha256(directory / "evaluator.py"),
            seed=cfg.seed,
            metric=cfg.metric,
            higher_is_better=cfg.higher_is_better,
            n_holdout=n_holdout,
        )
        (directory / "manifest.json").write_text(
            json.dumps(asdict(manifest), sort_keys=True) + "\n", encoding="utf-8",
        )
        Ledger(directory / "ledger.jsonl").append("seal", asdict(manifest))
        if mask is not None:
            _write_train(frame.loc[~mask], cfg.train_path, source_mode)
        for path in directory.iterdir():
            path.chmod(0o600 if path.name in {"ledger.jsonl", "head.txt"} else 0o400)
    except Exception:
        # Restore the original data before removing any partial sealed artifacts.
        if mask is not None and source.exists():
            shutil.move(str(source), cfg.train_path)
            cfg.train_path.chmod(source_mode)
        shutil.rmtree(directory)
        raise
    return manifest


def _split_mask(cfg: Config, frame: pd.DataFrame) -> np.ndarray:
    units = (
        pd.factorize(frame[cfg.group], use_na_sentinel=False)[0]
        if cfg.group is not None
        else np.arange(len(frame))
    )
    unique = np.unique(units)
    if len(unique) <= 1:
        msg = "Sealing requires at least two rows or groups"
        raise SealError(msg)
    count = max(1, min(len(unique) - 1, round(len(unique) * cfg.holdout_fraction)))
    selected = np.random.default_rng(cfg.seed).permutation(unique)[:count]
    return np.isin(units, selected)


def _load_external_holdout(path: Path, columns: list[str]) -> pd.DataFrame:
    if path.suffix not in {".csv", ".parquet"}:
        msg = "Holdout file must be CSV or Parquet"
        raise SealError(msg)
    try:
        hold = pd.read_csv(path) if path.suffix == ".csv" else pd.read_parquet(path)
    except Exception as exc:
        msg = f"Cannot read holdout file: {path}"
        raise SealError(msg) from exc
    if len(set(hold.columns)) != len(hold.columns) or set(hold.columns) != set(columns):
        msg = "Holdout file columns must match the training data columns"
        raise SealError(msg)
    if len(hold) == 0:
        msg = "Holdout file must have at least one row"
        raise SealError(msg)
    return hold[columns]


def _write_holdout(
    frame: pd.DataFrame,
    mask: np.ndarray | None,
    external: pd.DataFrame | None,
    src: Path | None,
    dest: Path,
) -> int:
    if mask is not None:
        frame.loc[mask].to_parquet(dest, index=False)
        return int(mask.sum())
    if external is None:
        msg = "No holdout rows to seal"
        raise SealError(msg)
    _write_external(external, src, dest)
    return len(external)


def _write_external(hold: pd.DataFrame, src: Path | None, dest: Path) -> None:
    if src is not None and src.suffix == ".parquet":
        original = pd.read_parquet(src)
        if list(original.columns) == list(hold.columns):
            shutil.copyfile(src, dest)
            return
    hold.to_parquet(dest, index=False)


def _write_train(frame: pd.DataFrame, path: Path, mode: int) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        train_temp = Path(temp.name)
    try:
        if path.suffix == ".csv":
            frame.to_csv(train_temp, index=False)
        else:
            frame.to_parquet(train_temp, index=False)
        train_temp.chmod(mode)
        os.replace(train_temp, path)  # noqa: PTH105
    finally:
        train_temp.unlink(missing_ok=True)


def verify_manifest(cfg: Config) -> bool:
    """Return True when the sealed files match the manifest hashes."""
    try:
        manifest = load_manifest(cfg)
        directory = store_dir(cfg)
        return (
            _sha256(directory / "holdout.parquet") == manifest.holdout_sha256
            and _sha256(directory / "evaluator.py") == manifest.evaluator_sha256
        )
    except (OSError, ValueError, TypeError):
        return False


def load_manifest(cfg: Config) -> Manifest:
    """Load the manifest from the sealed store."""
    data = json.loads((store_dir(cfg) / "manifest.json").read_text(encoding="utf-8"))
    return Manifest(**data)


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
