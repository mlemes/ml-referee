"""Load and validate the ml-referee TOML config."""

from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Raised when the config file is missing keys or has invalid values."""


@dataclass(frozen=True)
class Config:
    """Validated project configuration."""

    project_id: str
    train_path: Path
    label: str
    group: str | None
    eval_command: str
    metric: str
    higher_is_better: bool
    trial_patterns: tuple[str, ...]
    holdout_budget: int
    holdout_fraction: float
    seed: int


def load_config(path: Path) -> Config:
    """Parse and validate the TOML config at ``path``."""
    raise NotImplementedError


def store_dir(cfg: Config) -> Path:
    """Return the per-project data directory under XDG_DATA_HOME."""
    raise NotImplementedError
