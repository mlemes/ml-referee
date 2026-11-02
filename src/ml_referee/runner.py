"""Run the sealed evaluator against the holdout under a budget."""

from dataclasses import dataclass
from pathlib import Path

from ml_referee.config import Config

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
    raise NotImplementedError
