"""Tests for the leakage audit."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml_referee.audit.checks import (
    Finding,
    check_duplicates,
    check_entity_overlap,
    check_fit_on_full,
    check_target_leak,
)


def test_duplicate_rows_across_splits_flagged_high() -> None:
    train = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": ["x", "y", "z"]})
    hold = pd.DataFrame({"a": [2.0, 3.0, 9.0], "b": ["y", "z", "w"]})
    out = check_duplicates(train, hold)
    assert len(out) == 1
    assert out[0].check == "duplicates"
    assert out[0].severity == "high"
    assert "2" in out[0].detail


def test_clean_split_has_no_findings() -> None:
    train = pd.DataFrame({"a": [1.0, 2.0], "b": ["x", "y"]})
    hold = pd.DataFrame({"a": [5.0, 6.0], "b": ["p", "q"]})
    assert check_duplicates(train, hold) == []


def test_duplicates_use_shared_columns_and_normalize_floats() -> None:
    train = pd.DataFrame({"a": [0.1 + 0.2, 4.0], "extra": [1, 2]})
    hold = pd.DataFrame({"a": [0.3, 8.0], "other": ["u", "v"]})
    out = check_duplicates(train, hold)
    assert len(out) == 1
    assert out[0].severity == "high"
    assert "1" in out[0].detail


def test_entity_overlap_flagged() -> None:
    train = pd.DataFrame({"g": ["a", "b", "c"]})
    hold = pd.DataFrame({"g": ["b", "c", "d"]})
    out = check_entity_overlap(train, hold, "g")
    assert len(out) == 1
    assert out[0].severity == "high"
    assert "2" in out[0].detail


def test_entity_no_overlap_returns_empty() -> None:
    train = pd.DataFrame({"g": ["a", "b"]})
    hold = pd.DataFrame({"g": ["c", "d"]})
    assert check_entity_overlap(train, hold, "g") == []


def test_group_none_returns_single_info() -> None:
    df = pd.DataFrame({"g": ["a"]})
    out = check_entity_overlap(df, df, None)
    assert len(out) == 1
    assert out[0].severity == "info"


def test_group_column_absent_returns_info_not_crash() -> None:
    train = pd.DataFrame({"g": ["a"]})
    hold = pd.DataFrame({"h": ["a"]})
    for args in ((train, hold), (hold, train)):
        out = check_entity_overlap(args[0], args[1], "g")
        assert len(out) == 1
        assert out[0].severity == "info"


def test_group_column_with_nan_returns_finding_not_crash() -> None:
    train = pd.DataFrame({"g": ["a", np.nan, np.nan, "b"]})
    hold = pd.DataFrame({"g": ["a", "z"]})
    out = check_entity_overlap(train, hold, "g")
    sev = {f.severity for f in out}
    assert "medium" in sev
    assert "high" in sev
    medium = next(f for f in out if f.severity == "medium")
    assert "2" in medium.detail


def _frame(feature: np.ndarray, label: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame({"f": feature, "noise": np.arange(len(label)) % 3, "y": label})


def test_single_feature_with_auc_1_flagged_as_target_leak() -> None:
    y = np.array([0, 1] * 20)
    df = _frame(y * 10.0 + np.linspace(0, 0.1, 40), y)
    out = check_target_leak(df, "y")
    assert any(f.severity == "high" and "f" in f.detail for f in out)


def test_feature_with_auc_06_not_flagged() -> None:
    rng = np.random.default_rng(0)
    n = 200
    y = np.array([0] * (n // 2) + [1] * (n // 2))
    # Construct feature with AUC ~0.6.
    f = rng.normal(size=n) + 0.358 * y
    df = pd.DataFrame({"f": f, "y": y})
    assert check_target_leak(df, "y") == []


def test_inverted_perfect_separator_flagged() -> None:
    y = np.array([0, 1] * 20)
    df = pd.DataFrame({"f": -y * 5.0 + np.linspace(0, 0.1, 40), "y": y})
    out = check_target_leak(df, "y")
    assert any(f.severity == "high" and "f" in f.detail for f in out)


def test_non_binary_label_feature_equal_to_label_flagged() -> None:
    rng = np.random.default_rng(1)
    y = rng.normal(size=50)
    df = pd.DataFrame({"f": y.copy(), "n": rng.normal(size=50), "y": y})
    out = check_target_leak(df, "y")
    assert any(f.severity == "high" and "f" in f.detail for f in out)
    assert not any("'n'" in f.detail or f.detail.startswith("n ") for f in out)


def test_single_class_label_returns_info() -> None:
    df = pd.DataFrame({"f": [1.0, 2.0, 3.0], "y": [1, 1, 1]})
    out = check_target_leak(df, "y")
    assert len(out) == 1
    assert out[0].severity == "info"


def test_non_numeric_features_skipped() -> None:
    y = np.array([0, 1] * 10)
    df = pd.DataFrame({"s": ["a", "b"] * 10, "y": y})
    assert check_target_leak(df, "y") == []


def _write(tmp_path: Path, name: str, src: str) -> Path:
    p = tmp_path / name
    p.write_text(src)
    return p


def test_scaler_fit_before_split_flagged(tmp_path: Path) -> None:
    src = (
        "X = load()\nscaler = StandardScaler()\nXs = scaler.fit_transform(X)\n"
        "X_train, X_test = train_test_split(Xs)"
    )
    out = check_fit_on_full([_write(tmp_path, "a.py", src)])
    assert len(out) == 1
    assert out[0].severity == "high"


def test_scaler_fit_after_split_not_flagged(tmp_path: Path) -> None:
    src = "X_train, X_test = train_test_split(X)\nscaler.fit(X_train)"
    assert check_fit_on_full([_write(tmp_path, "a.py", src)]) == []


def test_syntax_error_file_returns_info(tmp_path: Path) -> None:
    out = check_fit_on_full([_write(tmp_path, "bad.py", "def (:\n")])
    assert len(out) == 1
    assert out[0].severity == "info"


def test_fit_after_split_inside_function_not_flagged(tmp_path: Path) -> None:
    src = (
        "def run(X):\n"
        "    X_train, X_test = train_test_split(X)\n"
        "    scaler.fit(X_train)\n"
    )
    assert check_fit_on_full([_write(tmp_path, "a.py", src)]) == []


def test_file_without_split_not_flagged(tmp_path: Path) -> None:
    src = "scaler = StandardScaler()\nscaler.fit(X)\n"
    assert check_fit_on_full([_write(tmp_path, "a.py", src)]) == []


def test_finding_is_frozen_dataclass() -> None:
    f = Finding("c", "info", "d")
    assert (f.check, f.severity, f.detail) == ("c", "info", "d")


@pytest.mark.parametrize(
    "src",
    [
        "X_alias = X\nscaler.fit(X_alias)\nX_train, X_test = train_test_split(X)",
        "Xs: Array = scaler.fit_transform(X)\nX_train, X_test = train_test_split(Xs)",
        "pipe.fit(df[cols], y)\nX_train, X_test = train_test_split(df)",
        "scaler.fit(X.values)\ntrain_test_split(X)",
        "scaler.fit(X=X)\ntrain_test_split(X)",
    ],
)
def test_fit_on_full_variants_flagged_high(tmp_path: Path, src: str) -> None:
    out = check_fit_on_full([_write(tmp_path, "a.py", src)])
    assert len(out) == 1
    assert out[0].severity == "high"


def test_unrelated_earlier_fit_sharing_selector_not_flagged(tmp_path: Path) -> None:
    src = (
        "scaler.fit(other_df, cols)\nX = df[cols]\n"
        "X_train, X_test = train_test_split(X)"
    )
    assert check_fit_on_full([_write(tmp_path, "a.py", src)]) == []


def test_fit_after_split_on_training_part_not_flagged(tmp_path: Path) -> None:
    src = "X_train, X_test = train_test_split(X)\nscaler.fit(X_train.values)"
    assert check_fit_on_full([_write(tmp_path, "a.py", src)]) == []


def test_empty_frames_do_not_crash() -> None:
    empty = pd.DataFrame({"g": pd.Series([], dtype=object), "f": [], "y": []})
    assert isinstance(check_duplicates(empty, empty), list)
    assert isinstance(check_entity_overlap(empty, empty, "g"), list)
    assert isinstance(check_target_leak(empty, "y"), list)
