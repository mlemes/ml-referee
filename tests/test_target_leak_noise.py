"""Target-leak detection must survive label noise on the leaking feature."""

import numpy as np
import pandas as pd
import pytest

from ml_referee.audit.checks import check_target_leak

SEEDS = range(30)
N = 300


def _flagged(frame: pd.DataFrame, column: str) -> bool:
    return any(
        f.severity in {"medium", "high"} and f"{column!r}" in f.detail
        for f in check_target_leak(frame, "y")
    )


def _regression(seed: int, noise: float) -> pd.DataFrame:
    """Honest features plus ``leak`` = label + noise * sd(label) * N(0, 1)."""
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(N, 3))
    y = x @ np.array([1.0, 0.5, 0.25]) + rng.normal(size=N)
    leak = y + noise * y.std() * rng.normal(size=N)
    return pd.DataFrame({"a": x[:, 0], "b": x[:, 1], "c": x[:, 2], "leak": leak, "y": y})


def _honest(seed: int, r2: float, features: int) -> pd.DataFrame:
    """Label with a target R2 from ``features`` honest features; none is a leak."""
    rng = np.random.default_rng(1000 + seed)
    x = rng.normal(size=(N, features))
    signal = x.sum(axis=1) / np.sqrt(features)
    y = np.sqrt(r2) * signal + np.sqrt(1 - r2) * rng.normal(size=N)
    columns = {f"x{i}": x[:, i] for i in range(features)}
    return pd.DataFrame({**columns, "y": y})


@pytest.mark.parametrize("noise", [0.1, 0.25, 0.5])
def test_noisy_copy_of_regression_label_flagged_every_seed(noise: float) -> None:
    missed = [s for s in SEEDS if not _flagged(_regression(s, noise), "leak")]
    assert not missed


def test_noisy_copy_flag_names_only_the_leak() -> None:
    out = check_target_leak(_regression(0, 0.5), "y")
    assert [f.detail.split()[0] for f in out] == ["'leak'"]


@pytest.mark.parametrize("features", [1, 3])
def test_honest_strong_features_not_flagged(features: int) -> None:
    false_alarms = [
        s for s in SEEDS
        if check_target_leak(_honest(s, 0.6, features), "y")
    ]
    assert not false_alarms


def test_honest_weak_features_not_flagged() -> None:
    assert all(not check_target_leak(_honest(s, 0.2, 2), "y") for s in SEEDS)


@pytest.mark.parametrize("noise", [0.25, 0.5])
def test_noisy_copy_of_binary_label_flagged_every_seed(noise: float) -> None:
    def frame(seed: int) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        y = rng.integers(0, 2, size=N).astype(float)
        leak = y + noise * y.std() * rng.normal(size=N)
        return pd.DataFrame({"a": rng.normal(size=N), "leak": leak, "y": y})

    assert all(_flagged(frame(s), "leak") for s in SEEDS)


def test_small_sample_is_not_flagged_by_the_noisy_rule() -> None:
    rng = np.random.default_rng(0)
    y = rng.normal(size=12)
    frame = pd.DataFrame({"f": y + 0.5 * rng.normal(size=12), "y": y})
    assert not any(f.severity == "medium" for f in check_target_leak(frame, "y"))
