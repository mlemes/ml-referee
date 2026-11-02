"""Claim report and sealed claim runner."""

from pathlib import Path

from ml_referee.audit import Finding
from ml_referee.config import Config
from ml_referee.runner import EvalResult
from ml_referee.seal import Manifest


def build_report(  # noqa: PLR0913, PLR0917
    manifest: Manifest,
    result: EvalResult,
    trials: int,
    findings: list[Finding],
    validation_scores: list[float],
    p_value: float | None = None,
    higher_is_better: bool = True,  # noqa: FBT001, FBT002
) -> str:
    """Build the Markdown claim report."""
    raise NotImplementedError


def claim(
    cfg: Config,
    predictions: Path,
    validation_scores: list[float],
    p_value: float | None = None,
    findings: list[Finding] | None = None,
) -> str:
    """Verify the store, run one sealed eval, and return the report."""
    raise NotImplementedError
