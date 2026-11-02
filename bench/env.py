"""Shared attack environment."""

from dataclasses import dataclass
from pathlib import Path

from ml_referee.config import Config
from ml_referee.policy import Policy


@dataclass(frozen=True)
class AttackEnv:
    """Sealed fixture that every attack runs against."""

    cfg: Config
    policy: Policy
    store: Path
    workspace: Path
    predictions: Path


@dataclass(frozen=True)
class AttackResult:
    """Outcome of one attack under one condition."""

    blocked: int
    attempted: int


def make_env(tmp_path: Path) -> AttackEnv:
    """Build and seal a fixture under ``tmp_path`` (caller sets XDG_DATA_HOME)."""
    raise NotImplementedError
