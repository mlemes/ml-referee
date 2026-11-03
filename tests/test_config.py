from pathlib import Path

import pytest

from ml_referee.config import Config, ConfigError, load_config, store_dir

FULL = """
[project]
id = "proj1"

[data]
train = "data/train.csv"
label = "y"
group = "g"

[eval]
command = "python eval.py"
metric = "auc"
higher_is_better = true

[trials]
patterns = ["a*.py", "b*.py"]

[holdout]
fraction = 0.25
seed = 7
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "cfg.toml"
    p.write_text(text)
    return p


def test_load_valid_config(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, FULL))
    assert cfg.project_id == "proj1"
    assert cfg.train_path == tmp_path / "data/train.csv"
    assert cfg.label == "y"
    assert cfg.group == "g"
    assert cfg.eval_command == "python eval.py"
    assert cfg.metric == "auc"
    assert cfg.higher_is_better is True
    assert cfg.trial_patterns == ("a*.py", "b*.py")
    assert cfg.holdout_budget == 1
    assert cfg.holdout_fraction == 0.25
    assert cfg.seed == 7


def test_defaults_and_optional_group(tmp_path: Path) -> None:
    text = FULL.replace('group = "g"\n', "").replace(
        "[holdout]\nfraction = 0.25\nseed = 7\n", ""
    )
    cfg = load_config(_write(tmp_path, text))
    assert cfg.group is None
    assert cfg.holdout_budget == 1
    assert cfg.holdout_fraction == 0.2
    assert cfg.seed == 0


def test_missing_label_raises(tmp_path: Path) -> None:
    text = FULL.replace('label = "y"\n', "")
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, text))


def test_budget_below_one_raises(tmp_path: Path) -> None:
    text = FULL.replace("fraction = 0.25", "budget = 0\nfraction = 0.25")
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, text))


def test_fraction_out_of_range_raises(tmp_path: Path) -> None:
    for bad in ("0", "1", "1.5"):
        text = FULL.replace("fraction = 0.25", f"fraction = {bad}")
        with pytest.raises(ConfigError):
            load_config(_write(tmp_path, text))


def test_store_dir_uses_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    cfg = load_config(_write(tmp_path, FULL))
    assert isinstance(cfg, Config)
    d = store_dir(cfg)
    assert d.name == "proj1"
    assert xdg in d.parents
