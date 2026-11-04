"""Tests for the near-duplicate audit check."""

import numpy as np
import pandas as pd
import pytest

from ml_referee.audit import checks
from ml_referee.audit.checks import Finding, check_duplicates, check_near_duplicates


def _frames(n_train: int = 800, n_hold: int = 400, n_features: int = 6, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    columns = [f"x{i}" for i in range(n_features)]
    train = pd.DataFrame(rng.standard_normal((n_train, n_features)), columns=columns)
    hold = pd.DataFrame(rng.standard_normal((n_hold, n_features)), columns=columns)
    train["y"] = rng.standard_normal(n_train)
    hold["y"] = rng.standard_normal(n_hold)
    return train, hold


def _plant(train: pd.DataFrame, hold: pd.DataFrame, fraction: float, sigma: float, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    count = round(len(hold) * fraction)
    rows = hold.iloc[rng.choice(len(hold), size=count, replace=False)].copy()
    features = [c for c in train.columns if c != "y"]
    std = train[features].std().to_numpy()
    rows[features] = rows[features].to_numpy() + sigma * std * rng.standard_normal((count, len(features)))
    return pd.concat([train, rows], ignore_index=True)


def test_clean_data_has_no_finding() -> None:
    for seed in range(5):
        train, hold = _frames(seed=seed)
        assert check_near_duplicates(train, hold, "y") == []


def test_five_percent_near_duplicates_are_found() -> None:
    train, hold = _frames()
    out = check_near_duplicates(_plant(train, hold, 0.10, 0.01), hold, "y")
    assert len(out) == 1
    assert out[0].check == "near_duplicates"
    assert out[0].severity == "high"


def test_medium_severity_between_one_and_five_percent() -> None:
    train, hold = _frames()
    out = check_near_duplicates(_plant(train, hold, 0.03, 0.005), hold, "y")
    assert [f.severity for f in out] == ["medium"]


def test_exact_duplicates_stay_with_the_existing_check() -> None:
    train, hold = _frames()
    shared = pd.concat([train, hold.iloc[:30]], ignore_index=True)
    exact = check_duplicates(shared.drop(columns=["y"]), hold.drop(columns=["y"]))
    assert [f.check for f in exact] == ["duplicates"]
    assert exact[0].severity == "high"


def test_label_is_ignored() -> None:
    train, hold = _frames()
    planted = _plant(train, hold, 0.10, 0.01)
    first = check_near_duplicates(planted, hold, "y")
    shuffled = hold.copy()
    shuffled["y"] = np.random.default_rng(9).permutation(shuffled["y"].to_numpy())
    flipped = planted.copy()
    flipped["y"] = flipped["y"].to_numpy()[::-1]
    assert check_near_duplicates(flipped, shuffled, "y") == first
    assert check_near_duplicates(planted, hold.drop(columns=["y"]), "y") == first


def test_output_contains_counts_only() -> None:
    train, hold = _frames()
    out = check_near_duplicates(_plant(train, hold, 0.10, 0.01), hold, "y")
    assert "holdout rows" in out[0].detail
    assert not any(str(v) in out[0].detail for v in hold["x0"].head(5))


def test_large_input_is_subsampled_deterministically(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checks, "NEAR_DUP_MAX_ROWS", 300)
    train, hold = _frames(n_train=900, n_hold=500)
    planted = _plant(train, hold, 0.2, 0.01)
    first = check_near_duplicates(planted, hold, "y")
    second = check_near_duplicates(planted.copy(), hold.copy(), "y")
    assert first == second
    assert first
    assert "subsampled" in first[0].detail


def test_nonnumeric_only_returns_info() -> None:
    train = pd.DataFrame({"a": ["x", "y"], "y": [1, 2]})
    out = check_near_duplicates(train, train, "y")
    assert out == [Finding("near_duplicates", "info", "No shared numeric feature columns.")]


def test_missing_values_and_constant_columns_do_not_crash() -> None:
    train, hold = _frames()
    train["const"] = 1.0
    hold["const"] = 1.0
    train.loc[::7, "x0"] = np.nan
    hold.loc[::5, "x1"] = np.nan
    assert check_near_duplicates(train, hold, "y") == []


def test_too_few_rows_returns_info() -> None:
    train = pd.DataFrame({"x": [1.0], "y": [1]})
    hold = pd.DataFrame({"x": [1.0], "y": [1]})
    out = check_near_duplicates(train, hold, "y")
    assert len(out) == 1
    assert out[0].severity == "info"


def test_explicit_threshold_overrides_the_data_driven_one() -> None:
    train, hold = _frames()
    planted = _plant(train, hold, 0.10, 2.0)
    assert check_near_duplicates(planted, hold, "y") == []
    assert check_near_duplicates(planted, hold, "y", threshold=100.0)


def test_numeric_group_column_is_not_a_feature() -> None:
    train, hold = _frames()
    train["g"] = np.arange(len(train))
    hold["g"] = np.arange(10_000, 10_000 + len(hold))
    planted = _plant(train, hold, 0.10, 0.01)
    planted["g"] = np.arange(len(planted))
    assert check_near_duplicates(planted, hold, "y", group="g")
    assert not check_near_duplicates(planted, hold, "y")


def test_infinite_values_do_not_crash() -> None:
    train, hold = _frames()
    train.loc[3, "x0"] = np.inf
    hold.loc[4, "x1"] = -np.inf
    assert check_near_duplicates(train, hold, "y") == []


def test_repeated_training_rows_do_not_disable_the_check() -> None:
    train, hold = _frames()
    train = pd.concat([train, train.iloc[:50]], ignore_index=True)
    out = check_near_duplicates(_plant(train, hold, 0.10, 0.01), hold, "y")
    assert [f.severity for f in out] == ["high"]


def test_boolean_holdout_column_is_skipped() -> None:
    train, hold = _frames()
    train["flag"] = 1.0
    hold["flag"] = True
    assert check_near_duplicates(train, hold, "y") == []


# --- CLI wiring -----------------------------------------------------------------------------

import json  # noqa: E402
from pathlib import Path  # noqa: E402

from ml_referee.cli import main  # noqa: E402
from ml_referee.config import load_config, store_dir  # noqa: E402

_EVALUATOR = """import json, sys
import pandas as pd
h = pd.read_parquet(sys.argv[1])
p = pd.read_csv(sys.argv[2])
print(json.dumps({"value": float(((h["y"] - p["pred"]) ** 2).mean() ** 0.5), "n": len(h)}))
"""

_TOML = """[project]
id = "ndproj"

[data]
train = "train.csv"
label = "y"

[eval]
command = "python eval.py"
metric = "rmse"
higher_is_better = false

[trials]
patterns = []

[holdout]
fraction = 0.25
seed = 1
budget = 1
"""


@pytest.fixture
def nd_ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    work = tmp_path / "w"
    work.mkdir()
    rng = np.random.default_rng(4)
    frame = pd.DataFrame(rng.standard_normal((600, 5)), columns=[f"x{i}" for i in range(5)])
    frame["y"] = rng.standard_normal(600)
    frame.to_csv(work / "train.csv", index=False)
    (work / ".ml-referee.toml").write_text(_TOML)
    (work / "ev.py").write_text(_EVALUATOR)
    monkeypatch.chdir(work)
    assert main(["seal", "--evaluator", str(work / "ev.py")]) == 0
    return work


def _holdout(work: Path) -> pd.DataFrame:
    return pd.read_parquet(store_dir(load_config(work / ".ml-referee.toml")) / "holdout.parquet")


def _lines(out: str) -> list[dict[str, str]]:
    return [json.loads(x) for x in out.splitlines() if x.strip()]


def test_audit_flags_near_duplicates_of_the_sealed_holdout(
    nd_ws: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    train = pd.read_csv(nd_ws / "train.csv")
    planted = _plant(train, _holdout(nd_ws), 0.15, 0.01)
    planted.to_csv(nd_ws / "train.csv", index=False)
    capsys.readouterr()
    code = main(["audit"])
    hits = [x for x in _lines(capsys.readouterr().out) if x["check"] == "near_duplicates"]
    assert code == 3
    assert [x["severity"] for x in hits] == ["high"]


def test_audit_clean_split_has_no_near_duplicate_finding(
    nd_ws: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    capsys.readouterr()
    code = main(["audit"])
    out = _lines(capsys.readouterr().out)
    assert code == 0
    assert not [x for x in out if x["check"] == "near_duplicates"]


def test_audit_against_flags_near_duplicates_in_a_visible_file(
    nd_ws: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    train = pd.read_csv(nd_ws / "train.csv")
    rng = np.random.default_rng(8)
    visible = train.iloc[:60].copy()
    features = [c for c in train.columns if c != "y"]
    visible[features] = visible[features].to_numpy() + 0.005 * rng.standard_normal((60, 5))
    visible.to_csv(nd_ws / "visible.csv", index=False)
    capsys.readouterr()
    code = main(["audit", "--against", str(nd_ws / "visible.csv")])
    hits = [x for x in _lines(capsys.readouterr().out) if x["check"] == "near_duplicates"]
    assert code == 3
    assert len(hits) == 1
    assert hits[0]["detail"].endswith("[against=visible.csv]")


def test_claim_report_lists_the_near_duplicate_finding(
    nd_ws: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    train = pd.read_csv(nd_ws / "train.csv")
    hold = _holdout(nd_ws)
    _plant(train, hold, 0.15, 0.01).to_csv(nd_ws / "train.csv", index=False)
    pd.DataFrame({"pred": np.zeros(len(hold))}).to_csv(nd_ws / "p.csv", index=False)
    capsys.readouterr()
    assert main(["claim", "--predictions", str(nd_ws / "p.csv"), "--out", str(nd_ws / "claim.md")]) == 0
    report = (nd_ws / "claim.md").read_text()
    assert "- high: near_duplicates:" in report
