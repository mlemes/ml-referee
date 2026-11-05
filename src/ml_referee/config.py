"""Load and validate the ml-referee TOML config."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar, cast

_T = TypeVar("_T", str, int, bool, list[object])


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
    workspace: Path = Path()
    predict_timeout_s: int = 600
    group_hidden: bool = False
    claim_reserve: int = 0


def load_config(path: Path) -> Config:  # noqa: C901
    """Parse and validate the TOML config at ``path``."""
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, ValueError) as exc:
        msg = f"Cannot read config {path}: {exc}"
        raise ConfigError(msg) from exc

    def value(section: str, key: str, kind: type[_T], default: object = None) -> _T:
        table = raw.get(section, {})
        if not isinstance(table, dict):
            msg = f"{section} must be a table"
            raise ConfigError(msg)
        result = table.get(key, default)
        if not isinstance(result, kind) or (kind is int and isinstance(result, bool)):
            msg = f"{section}.{key} must be {kind.__name__}"
            raise ConfigError(msg)
        return result

    project_id = value("project", "id", str)
    if (
        not project_id
        or project_id in {".", ".."}
        or "/" in project_id
        or "\\" in project_id
    ):
        msg = "project.id must be a nonempty directory name"
        raise ConfigError(msg)
    budget = value("holdout", "budget", int, 1)
    fraction = raw.get("holdout", {}).get("fraction", 0.2)
    if budget < 1:
        msg = "holdout.budget must be at least 1"
        raise ConfigError(msg)
    if (
        isinstance(fraction, bool)
        or not isinstance(fraction, (int, float))
        or not 0 < fraction < 1
    ):
        msg = "holdout.fraction must be between 0 and 1"
        raise ConfigError(msg)
    patterns = value("trials", "patterns", list)
    if not all(isinstance(pattern, str) for pattern in patterns):
        msg = "trials.patterns must contain strings"
        raise ConfigError(msg)
    train = value("data", "train", str)
    group = raw["data"].get("group")
    if group is not None and not isinstance(group, str):
        msg = "data.group must be a string"
        raise ConfigError(msg)
    timeout = value("eval", "predict_timeout_s", int, 600)
    if timeout < 1:
        msg = "eval.predict_timeout_s must be at least 1"
        raise ConfigError(msg)
    reserve = value("eval", "claim_reserve", int, 1 if budget >= 2 else 0)  # noqa: PLR2004
    if not 0 <= reserve < budget:
        msg = "eval.claim_reserve must be at least 0 and below holdout.budget"
        raise ConfigError(msg)
    return Config(
        project_id=project_id,
        train_path=path.parent / train,
        label=value("data", "label", str),
        group=group,
        eval_command=value("eval", "command", str),
        metric=value("eval", "metric", str),
        higher_is_better=value("eval", "higher_is_better", bool),
        trial_patterns=tuple(cast("list[str]", patterns)),
        holdout_budget=budget,
        holdout_fraction=float(fraction),
        seed=value("holdout", "seed", int, 0),
        workspace=path.parent,
        predict_timeout_s=timeout,
        group_hidden=value("data", "group_hidden", bool, False),  # noqa: FBT003
        claim_reserve=reserve,
    )


def find_config(start: Path) -> Path | None:
    """Find the nearest config, starting at a workspace directory."""
    workspace = start.resolve()
    for directory in (workspace, *workspace.parents):
        config = directory / ".ml-referee.toml"
        try:
            config.lstat()
        except FileNotFoundError:
            continue
        return config
    return None


def store_root() -> Path:
    """Return the directory that holds every project store (not resolved)."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "ml-referee"


def store_dir(cfg: Config) -> Path:
    """Return the per-project data directory under XDG_DATA_HOME."""
    return store_root() / cfg.project_id
