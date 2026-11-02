"""Leakage audit checks."""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class Finding:
    """One audit finding."""

    check: str
    severity: str
    detail: str


def check_duplicates(train: pd.DataFrame, holdout_keys: pd.DataFrame) -> list[Finding]:
    """Flag exact duplicate rows across splits."""
    raise NotImplementedError


def check_entity_overlap(
    train: pd.DataFrame, holdout: pd.DataFrame, group: str | None,
) -> list[Finding]:
    """Flag group entities present in both splits."""
    raise NotImplementedError


def check_target_leak(
    train: pd.DataFrame, label: str, auc_threshold: float = 0.98,
) -> list[Finding]:
    """Flag features that nearly separate the label."""
    raise NotImplementedError


def check_fit_on_full(sources: list[Path]) -> list[Finding]:
    """Flag fit/fit_transform on data before the split."""
    raise NotImplementedError
