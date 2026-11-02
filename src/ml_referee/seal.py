"""Seal the holdout split and the evaluator outside the workspace."""

from dataclasses import dataclass
from pathlib import Path

from ml_referee.config import Config


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


def seal(cfg: Config, evaluator_src: Path) -> Manifest:
    """Move the holdout rows and the evaluator into the sealed store."""
    raise NotImplementedError


def verify_manifest(cfg: Config) -> bool:
    """Return True when the sealed files match the manifest hashes."""
    raise NotImplementedError


def load_manifest(cfg: Config) -> Manifest:
    """Load the manifest from the sealed store."""
    raise NotImplementedError
