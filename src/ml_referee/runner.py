"""Run the sealed evaluator against the holdout under a budget."""

import fcntl
import hashlib
import json
import math
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from ml_referee.config import Config, store_dir
from ml_referee.ledger import Ledger
from ml_referee.seal import Manifest, load_manifest, verify_manifest

EVAL_TIMEOUT_S: int = 60
PREDICT_OUTPUT_LIMIT: int = 1000


class BudgetExhausted(RuntimeError):  # noqa: N818
    """Raised when the holdout budget is spent."""


class IntegrityError(RuntimeError):
    """Raised when sealed files, the ledger, or evaluator output are inconsistent."""


class EvaluatorError(RuntimeError):
    """Raised when the evaluator fails, times out, or prints invalid output."""


class PredictorError(RuntimeError):
    """Raised when the predict command fails, times out, or writes bad output."""


@dataclass(frozen=True)
class EvalResult:
    """Result of one holdout evaluation."""

    metric: str
    value: float
    n: int


@contextmanager
def _budget_lock(cfg: Config) -> Iterator[tuple[Path, Manifest, Ledger]]:
    """Hold the eval lock with a verified store and an unspent budget."""
    directory = store_dir(cfg)
    _verify_store(cfg, directory)
    directory = directory.resolve(strict=True)
    with os.fdopen(
        os.open(directory / "eval.lock", os.O_CREAT | os.O_RDWR, 0o600),
        "a",
    ) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = _verify_store(cfg, directory)
        ledger = Ledger(directory / "ledger.jsonl")
        if ledger.eval_count() >= cfg.holdout_budget:
            msg = "Holdout evaluation budget exhausted"
            raise BudgetExhausted(msg)
        yield directory, manifest, ledger


def run_holdout_eval(cfg: Config, predictions: Path) -> EvalResult:
    """Run the sealed evaluator once and record it in the ledger."""
    with _budget_lock(cfg) as (directory, manifest, ledger):
        try:
            predictions = predictions.resolve(strict=True)
            if not predictions.is_file() or predictions.is_relative_to(directory):
                msg = "Predictions must be a file outside sealed store"
                raise EvaluatorError(msg) from None
        except (OSError, RuntimeError):
            msg = "Predictions must be a file outside sealed store"
            raise EvaluatorError(msg) from None
        return _score(directory, manifest, ledger, predictions, {})


def run_predict_eval(cfg: Config, predict_cmd: str) -> EvalResult:
    """Score a predictor on the sealed rows without showing it their labels.

    ``predict_cmd`` holds ``{features}`` and ``{out}``. The runner writes the
    sealed rows without the label (and without the group column when the
    config hides it) to a private temp file, runs the command in the workspace
    without a shell, validates the predictions it wrote, and scores a copy of
    them. A failure before scoring spends no budget. The temp files are always
    removed. The predictor runs as the same user and sees the unlabeled rows.
    """
    try:
        args = shlex.split(predict_cmd)
    except ValueError:
        args = []
    joined = " ".join(args)
    if not args or "{features}" not in joined or "{out}" not in joined:
        msg = "The predict command must name {features} and {out}"
        raise ValueError(msg)
    with _budget_lock(cfg) as (directory, manifest, ledger):
        temp = Path(tempfile.mkdtemp(prefix="ml-referee-"))
        try:
            temp.chmod(0o700)
            features, out, scored = (temp / name for name in (
                "features.csv", "predictions.csv", "scored.csv",
            ))
            hidden = [cfg.label]
            if cfg.group_hidden and cfg.group:
                hidden.append(cfg.group)
            pd.read_parquet(directory / "holdout.parquet").drop(
                columns=hidden,
            ).to_csv(features, index=False)
            _run_predictor(cfg, args, features, out)
            _read_predictions(out, manifest.n_holdout).to_csv(scored, index=False)
            digest = hashlib.sha256(predict_cmd.encode()).hexdigest()
            return _score(
                directory, manifest, ledger, scored,
                {"predict_cmd_sha256": digest},
            )
        finally:
            shutil.rmtree(temp, ignore_errors=True)


def _run_predictor(cfg: Config, args: list[str], features: Path, out: Path) -> None:
    """Run the predict command in the workspace; kill its process group."""
    argv = [
        arg.replace("{features}", str(features)).replace("{out}", str(out))
        for arg in args
    ]
    log = out.with_name("output.log")
    try:
        # A file, not a pipe: a background child holding a pipe would block the wait.
        with log.open("wb") as sink:
            process = subprocess.Popen(  # noqa: S603
                argv,
                cwd=cfg.workspace,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError as exc:
        msg = f"Predict command could not start: {exc}"
        raise PredictorError(msg) from None
    try:
        process.wait(timeout=cfg.predict_timeout_s)
    except subprocess.TimeoutExpired:
        msg = f"Predict command timed out after {cfg.predict_timeout_s} s"
        raise PredictorError(msg) from None
    finally:
        # Stray children of the predictor must not outlive the run.
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    if process.returncode != 0:
        with log.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - PREDICT_OUTPUT_LIMIT))
            tail = stream.read().decode("utf-8", "replace")
        msg = f"Predict command exited with {process.returncode}: {tail}"
        raise PredictorError(msg)


def _read_predictions(path: Path, rows: int) -> pd.DataFrame:
    """Read one predictions column; reject wrong length or missing values."""
    try:
        frame = pd.read_csv(path)
    except (OSError, ValueError, pd.errors.ParserError):
        msg = "Predict command wrote no readable predictions file"
        raise PredictorError(msg) from None
    if frame.shape[1] != 1:
        msg = "Predictions must have exactly one column"
        raise PredictorError(msg)
    if len(frame) != rows:
        msg = f"Predictions must have {rows} rows, got {len(frame)}"
        raise PredictorError(msg)
    column = frame.iloc[:, 0]
    if column.isna().any():
        msg = "Predictions must not contain missing values"
        raise PredictorError(msg)
    if pd.api.types.is_numeric_dtype(column) and not math.isfinite(
        float(column.abs().max()),
    ):
        msg = "Predictions must be finite"
        raise PredictorError(msg)
    return frame


def _score(
    directory: Path,
    manifest: Manifest,
    ledger: Ledger,
    predictions: Path,
    extra: dict[str, object],
) -> EvalResult:
    """Run the sealed evaluator on ``predictions`` and append the eval record."""
    try:
        process = subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                str(directory / "evaluator.py"),
                str(directory / "holdout.parquet"),
                str(predictions),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env={"PATH": os.environ.get("PATH", "")},
        )
        try:
            stdout, _ = process.communicate(timeout=EVAL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            msg = "Evaluator timed out"
            raise EvaluatorError(msg) from None
    except OSError:
        msg = "Evaluator could not complete"
        raise EvaluatorError(msg) from None
    if process.returncode != 0:
        msg = "Evaluator exited unsuccessfully"
        raise EvaluatorError(msg) from None
    value, n = _parse_output(stdout)
    if n != manifest.n_holdout:
        msg = "Evaluator row count does not match sealed holdout"
        raise IntegrityError(msg) from None
    result = EvalResult(metric=manifest.metric, value=float(value), n=n)
    try:
        ledger.append("eval", {**asdict(result), **extra})
    except (OSError, ValueError, TypeError):
        msg = "Could not record evaluation in ledger"
        raise IntegrityError(msg) from None
    return result


def _verify_store(cfg: Config, directory: Path) -> Manifest:
    """Require an intact store whose first ledger entry binds its manifest."""
    try:
        if (
            not directory.is_dir()
            or not (directory / "ledger.jsonl").is_file()
            or not (directory / "head.txt").is_file()
        ):
            msg = "Sealed store is incomplete"
            raise IntegrityError(msg) from None
        ledger = Ledger(directory / "ledger.jsonl")
        if not ledger.verify().ok or not verify_manifest(cfg):
            msg = "Sealed files or ledger failed integrity verification"
            raise IntegrityError(msg) from None
        manifest = load_manifest(cfg)
        records = ledger.records()
        if (
            not records
            or records[0].kind != "seal"
            or records[0].payload != asdict(manifest)
        ):
            msg = "Ledger seal does not match manifest"
            raise IntegrityError(msg) from None
    except (OSError, ValueError, TypeError):
        msg = "Sealed store failed integrity verification"
        raise IntegrityError(msg) from None
    return manifest


def _parse_output(stdout: bytes) -> tuple[float, int]:
    """Validate evaluator output without exposing its contents in errors."""
    try:
        output = json.loads(stdout)
    except ValueError:
        msg = "Evaluator returned invalid output"
        raise EvaluatorError(msg) from None
    if not isinstance(output, dict):
        msg = "Evaluator returned invalid output"
        raise EvaluatorError(msg) from None
    value, n = output.get("value"), output.get("n")
    if type(value) not in (int, float) or type(n) is not int or n < 1:
        msg = "Evaluator returned invalid output"
        raise EvaluatorError(msg) from None
    try:
        value = float(output["value"])
    except OverflowError:
        msg = "Evaluator returned invalid output"
        raise EvaluatorError(msg) from None
    if not math.isfinite(value):
        msg = "Evaluator returned invalid output"
        raise EvaluatorError(msg) from None
    return value, n
