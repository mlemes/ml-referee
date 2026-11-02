"""Run the sealed evaluator against the holdout under a budget."""

import fcntl
import json
import math
import os
import signal
import subprocess
import sys
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

from ml_referee.config import Config, store_dir
from ml_referee.ledger import Ledger
from ml_referee.seal import Manifest, load_manifest, verify_manifest

EVAL_TIMEOUT_S: int = 60


class BudgetExhausted(RuntimeError):  # noqa: N818
    """Raised when the holdout budget is spent."""


class IntegrityError(RuntimeError):
    """Raised when sealed files, the ledger, or evaluator output are inconsistent."""


class EvaluatorError(RuntimeError):
    """Raised when the evaluator fails, times out, or prints invalid output."""


@dataclass(frozen=True)
class EvalResult:
    """Result of one holdout evaluation."""

    metric: str
    value: float
    n: int


def run_holdout_eval(cfg: Config, predictions: Path) -> EvalResult:
    """Run the sealed evaluator once and record it in the ledger."""
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
        try:
            predictions = predictions.resolve(strict=True)
            if not predictions.is_file() or predictions.is_relative_to(directory):
                msg = "Predictions must be a file outside sealed store"
                raise EvaluatorError(msg) from None
        except (OSError, RuntimeError):
            msg = "Predictions must be a file outside sealed store"
            raise EvaluatorError(msg) from None
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
            ledger.append("eval", asdict(result))
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
