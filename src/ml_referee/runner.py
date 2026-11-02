"""Run the sealed evaluator against the holdout under a budget."""

import fcntl
import json
import math
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from ml_referee.config import Config, store_dir
from ml_referee.ledger import Ledger
from ml_referee.seal import load_manifest, verify_manifest

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
    with (directory / "eval.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = Ledger(directory / "ledger.jsonl")
        if not ledger.verify().ok or not verify_manifest(cfg):
            msg = "Sealed files or ledger failed integrity verification"
            raise IntegrityError(msg)
        manifest = load_manifest(cfg)
        if ledger.eval_count() >= cfg.holdout_budget:
            msg = "Holdout evaluation budget exhausted"
            raise BudgetExhausted(msg)
        try:
            completed = subprocess.run(  # noqa: S603
                [
                    sys.executable,
                    str(directory / "evaluator.py"),
                    str(directory / "holdout.parquet"),
                    str(predictions),
                ],
                capture_output=True,
                timeout=EVAL_TIMEOUT_S,
                env={"PATH": os.environ.get("PATH", "")},
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            msg = "Evaluator could not complete"
            raise EvaluatorError(msg) from None
        if completed.returncode != 0:
            msg = "Evaluator exited unsuccessfully"
            raise EvaluatorError(msg)
        value, n = _parse_output(completed.stdout)
        if n != manifest.n_holdout:
            msg = "Evaluator row count does not match sealed holdout"
            raise IntegrityError(msg)
        result = EvalResult(metric=manifest.metric, value=float(value), n=n)
        try:
            ledger.append("eval", asdict(result))
        except (OSError, ValueError, TypeError):
            msg = "Could not record evaluation in ledger"
            raise IntegrityError(msg) from None
        return result


def _parse_output(stdout: bytes) -> tuple[float, int]:
    """Validate evaluator output without exposing its contents in errors."""
    try:
        output = json.loads(stdout)
    except ValueError:
        msg = "Evaluator returned invalid output"
        raise EvaluatorError(msg) from None
    if not isinstance(output, dict):
        msg = "Evaluator returned invalid output"
        raise EvaluatorError(msg)
    value, n = output.get("value"), output.get("n")
    if type(value) not in (int, float) or type(n) is not int or n < 1:
        msg = "Evaluator returned invalid output"
        raise EvaluatorError(msg)
    try:
        value = float(output["value"])
    except OverflowError:
        msg = "Evaluator returned invalid output"
        raise EvaluatorError(msg) from None
    if not math.isfinite(value):
        msg = "Evaluator returned invalid output"
        raise EvaluatorError(msg)
    return value, n
