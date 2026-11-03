import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml_referee.cli import main
from ml_referee.config import load_config, store_dir
from ml_referee.ledger import Ledger

EVALUATOR = """import json, sys
import pandas as pd
h = pd.read_parquet(sys.argv[1])
p = pd.read_csv(sys.argv[2])
acc = float((h["y"].to_numpy() == p["pred"].to_numpy()).mean())
print(json.dumps({"value": acc, "n": len(h)}))
"""

TOML = """[project]
id = "cliproj"

[data]
train = "data/train.csv"
label = "y"

[eval]
command = "python eval.py"
metric = "acc"
higher_is_better = true

[trials]
patterns = ["python train.py*"]

[holdout]
fraction = 0.25
seed = 1
budget = 1
"""

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    work = tmp_path / "w"
    (work / "data").mkdir(parents=True)
    rng = np.random.default_rng(3)
    n = 100
    pd.DataFrame(
        {"x": rng.normal(size=n), "y": rng.integers(0, 2, size=n)}
    ).to_csv(work / "data" / "train.csv", index=False)
    (work / ".ml-referee.toml").write_text(TOML)
    (work / "ev_src.py").write_text(EVALUATOR)
    monkeypatch.chdir(work)
    return work


def _seal(ws: Path, capsys: pytest.CaptureFixture[str]) -> str:
    code = main(["seal", "--evaluator", str(ws / "ev_src.py")])
    out = capsys.readouterr().out
    assert code == 0
    return out


def _preds(ws: Path) -> Path:
    hold = pd.read_parquet(store_dir(load_config(ws / ".ml-referee.toml")) / "holdout.parquet")
    p = ws / "preds.csv"
    pd.DataFrame({"pred": hold["y"].to_numpy()}).to_csv(p, index=False)
    return p


def _ledger(ws: Path) -> Ledger:
    cfg = load_config(ws / ".ml-referee.toml")
    return Ledger(store_dir(cfg) / "ledger.jsonl")


def test_seal_prints_only_holdout_count(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _seal(ws, capsys)
    assert "25" in out
    assert len(pd.read_csv(ws / "data" / "train.csv")) == 75


def test_eval_prints_json_then_second_call_exits_nonzero(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seal(ws, capsys)
    preds = _preds(ws)
    assert main(["eval", "--predictions", str(preds)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {"metric": "acc", "value": 1.0, "n": 25}
    assert main(["eval", "--predictions", str(preds)]) == 1
    cap = capsys.readouterr()
    assert cap.err
    assert "value" not in cap.out


def test_eval_explicit_config_path(
    ws: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seal(ws, capsys)
    preds = _preds(ws)
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    code = main(["eval", "--config", str(ws / ".ml-referee.toml"), "--predictions", str(preds)])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["n"] == 25


def test_audit_prints_json_lines_counts_only(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Leaky feature: copy of label in train.
    df = pd.read_csv(ws / "data" / "train.csv")
    df["leak"] = df["y"]
    df.to_csv(ws / "data" / "train.csv", index=False)
    _seal(ws, capsys)
    code = main(["audit"])
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert lines
    for line in lines:
        assert set(line) == {"check", "severity", "detail"}
    leak = [x for x in lines if x["check"] == "target_leak"]
    assert leak
    assert any(x["severity"] == "high" for x in lines)
    assert code == 3


def test_audit_clean_exits_zero(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seal(ws, capsys)
    code = main(["audit"])
    out = capsys.readouterr().out
    for line in out.splitlines():
        if line.strip():
            assert json.loads(line)["severity"] != "high"
    assert code == 0


def test_audit_sources_flags_fit_on_full(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seal(ws, capsys)
    src = ws / "train.py"
    src.write_text(
        "from sklearn.preprocessing import StandardScaler\n"
        "from sklearn.model_selection import train_test_split\n"
        "import pandas as pd\n"
        "df = pd.read_csv('d.csv')\n"
        "X = StandardScaler().fit_transform(df)\n"
        "a, b = train_test_split(X)\n"
    )
    main(["audit", "--sources", str(src)])
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert any(x["check"] == "fit_on_full" for x in lines)


def test_verify_ledger_exit_codes(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seal(ws, capsys)
    assert main(["verify-ledger"]) == 0
    capsys.readouterr()
    path = _ledger(ws).path
    path.chmod(0o600)
    text = path.read_text()
    path.write_text(text.replace('"seed": 1', '"seed": 2'))
    assert main(["verify-ledger"]) == 1
    out = capsys.readouterr().out.strip()
    result = json.loads(out)
    assert result["ok"] is False


def test_claim_writes_out_file(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seal(ws, capsys)
    out_file = ws / "report.md"
    code = main(["claim", "--predictions", str(_preds(ws)), "--out", str(out_file)])
    assert code == 0
    assert "Hooks are a policy layer, not a sandbox." in out_file.read_text()


def test_claim_bad_p_value_exits_one(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seal(ws, capsys)
    code = main(["claim", "--predictions", str(_preds(ws)), "--p-value", "2.0"])
    assert code == 1
    assert capsys.readouterr().err


def test_end_to_end_seal_eval_claim(
    ws: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _seal(ws, capsys)
    env = {**os.environ, "XDG_DATA_HOME": str(tmp_path / "xdg"),
           "PYTHONPATH": str(ROOT / "src")}
    event = json.dumps({
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "python train.py --lr 1"},
        "tool_response": {"stdout": "ok", "exit_code": 0},
        "cwd": str(ws),
    })
    for _ in range(2):
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "ml_referee.hooks.recorder"],
            input=event, text=True, env=env, cwd=ws, capture_output=True, check=False,
        )
        assert proc.returncode == 0
    assert _ledger(ws).trial_count() == 2
    vals = ws / "vals.json"
    vals.write_text(json.dumps([0.9, 0.95, 0.92]))
    preds = _preds(ws)
    code = main(["claim", "--predictions", str(preds), "--val-scores", str(vals),
                 "--p-value", "0.01"])
    out = capsys.readouterr().out
    assert code == 0
    assert "## Trials\n\n2" in out
    assert "Hooks are a policy layer, not a sandbox." in out
    assert "Bonferroni-adjusted p-value: 0.02" in out
    assert "Selection gap" in out
    assert main(["claim", "--predictions", str(preds)]) == 1
    assert capsys.readouterr().err


def test_claim_clean_data_does_not_say_findings_supplied(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seal(ws, capsys)
    code = main(["claim", "--predictions", str(_preds(ws))])
    report = capsys.readouterr().out
    assert code == 0
    assert "## Audit findings" in report
    assert "No audit findings supplied." not in report


def test_claim_reports_duplicate_findings(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    train = ws / "data" / "train.csv"
    df = pd.read_csv(train)
    pd.concat([df, df], ignore_index=True).to_csv(train, index=False)
    _seal(ws, capsys)
    code = main(["claim", "--predictions", str(_preds(ws))])
    report = capsys.readouterr().out
    assert code == 0
    section = report.split("## Audit findings")[1].split("## Limits")[0]
    assert "- high: duplicates:" in section
    assert "No audit findings supplied." not in report


def _add_holdout_to_train_flipped(ws: Path) -> None:
    cfg = load_config(ws / ".ml-referee.toml")
    hold = pd.read_parquet(store_dir(cfg) / "holdout.parquet")
    flipped = hold.copy()
    flipped["y"] = 1 - flipped["y"]
    train = pd.read_csv(ws / "data" / "train.csv")
    pd.concat([train, flipped[train.columns]], ignore_index=True).to_csv(
        ws / "data" / "train.csv", index=False
    )


def test_audit_duplicates_ignore_label(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seal(ws, capsys)
    _add_holdout_to_train_flipped(ws)
    code = main(["audit"])
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    dups = [x for x in lines if x["check"] == "duplicates"]
    assert dups
    assert dups[0]["severity"] == "high"
    assert code == 3


def test_claim_duplicates_ignore_label(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seal(ws, capsys)
    _add_holdout_to_train_flipped(ws)
    code = main(["claim", "--predictions", str(_preds(ws))])
    report = capsys.readouterr().out
    assert code == 0
    assert "- high: duplicates:" in report


def test_claim_bad_out_does_not_consume_budget(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seal(ws, capsys)
    preds = _preds(ws)
    missing = ws / "nope" / "report.md"
    assert main(["claim", "--predictions", str(preds), "--out", str(missing)]) == 1
    sealed = store_dir(load_config(ws / ".ml-referee.toml")) / "report.md"
    assert main(["claim", "--predictions", str(preds), "--out", str(sealed)]) == 1
    assert not sealed.exists()
    assert _ledger(ws).eval_count() == 0
    good = ws / "report.md"
    assert main(["claim", "--predictions", str(preds), "--out", str(good)]) == 0
    assert good.exists()
    assert _ledger(ws).eval_count() == 1


def test_audit_output_has_no_holdout_label_values(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seal(ws, capsys)
    _add_holdout_to_train_flipped(ws)
    main(["audit"])
    out = capsys.readouterr().out
    cfg = load_config(ws / ".ml-referee.toml")
    hold = pd.read_parquet(store_dir(cfg) / "holdout.parquet")
    for line in out.splitlines():
        detail = json.loads(line)["detail"]
        for value in hold["y"].unique():
            assert f"y={value}" not in detail
            assert f"label {value}" not in detail


def test_claim_out_directory_exits_one_then_good_out_succeeds(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seal(ws, capsys)
    preds = _preds(ws)
    d = ws / "outdir"
    d.mkdir()
    assert main(["claim", "--predictions", str(preds), "--out", str(d)]) == 1
    assert _ledger(ws).eval_count() == 0
    good = ws / "report.md"
    assert main(["claim", "--predictions", str(preds), "--out", str(good)]) == 0
    assert _ledger(ws).eval_count() == 1


def test_claim_out_readonly_file_exits_one_without_budget(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seal(ws, capsys)
    preds = _preds(ws)
    ro = ws / "ro.md"
    ro.write_text("old")
    ro.chmod(0o400)
    assert main(["claim", "--predictions", str(preds), "--out", str(ro)]) == 1
    assert ro.read_text() == "old"
    assert _ledger(ws).eval_count() == 0


def _holdout_csv(ws: Path) -> Path:
    path = ws / "hold.csv"
    pd.DataFrame({"x": [0.1, 0.2, 0.3], "y": [1, 0, 1]}).to_csv(path, index=False)
    return path


def test_seal_holdout_file_option(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    train = ws / "data" / "train.csv"
    before = train.read_bytes()
    hold = _holdout_csv(ws)
    code = main(["seal", "--evaluator", str(ws / "ev_src.py"),
                 "--holdout-file", str(hold)])
    cap = capsys.readouterr()
    assert code == 0
    assert "3" in cap.out
    assert train.read_bytes() == before
    sealed = pd.read_parquet(store_dir(load_config(ws / ".ml-referee.toml")) / "holdout.parquet")
    assert list(sealed["y"]) == [1, 0, 1]
    assert _ledger(ws).eval_count() == 0


def test_seal_holdout_file_bad_columns_exits_one_no_store(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = ws / "bad.csv"
    pd.DataFrame({"x": [0.1], "z": [1]}).to_csv(bad, index=False)
    code = main(["seal", "--evaluator", str(ws / "ev_src.py"), "--holdout-file", str(bad)])
    cap = capsys.readouterr()
    assert code == 1
    assert cap.err
    assert "NotImplementedError" not in cap.err
    assert not store_dir(load_config(ws / ".ml-referee.toml")).exists()


def test_seal_holdout_file_missing_exits_one_no_store(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["seal", "--evaluator", str(ws / "ev_src.py"),
                 "--holdout-file", str(ws / "nope.csv")])
    cap = capsys.readouterr()
    assert code == 1
    assert cap.err
    assert not store_dir(load_config(ws / ".ml-referee.toml")).exists()
