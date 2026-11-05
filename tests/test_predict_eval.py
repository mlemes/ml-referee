"""``ml-referee eval --predict-cmd``: the runner feeds unlabeled sealed rows to a predictor."""

import hashlib
import json
import os
import shlex
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml_referee import runner
from ml_referee.cli import main
from ml_referee.config import Config, ConfigError, load_config, store_dir
from ml_referee.ledger import Ledger
from ml_referee.runner import (
    BudgetExhausted,
    EvaluatorError,
    PredictorError,
    run_predict_eval,
)
from ml_referee.seal import seal

EVALUATOR = """import json, sys
import pandas as pd
h = pd.read_parquet(sys.argv[1])
p = pd.read_csv(sys.argv[2])
acc = float((h["y"].to_numpy() == p["pred"].to_numpy()).mean())
print(json.dumps({"value": acc, "n": len(h)}))
"""
CONST = "import sys\nimport pandas as pd\n" \
    "f = pd.read_csv(sys.argv[1])\npd.DataFrame({'pred': [0] * len(f)}).to_csv(sys.argv[2], index=False)\n"


class Env:
    """Sealed project with a workspace and helpers to run predictors."""

    def __init__(self, work: Path, cfg: Config) -> None:
        self.work, self.cfg = work, cfg

    def script(self, body: str, name: str = "predict.py") -> str:
        (self.work / name).write_text(body)
        return f"{shlex.quote(sys.executable)} {self.work / name} {{features}} {{out}}"

    @property
    def holdout(self) -> pd.DataFrame:
        return pd.read_parquet(store_dir(self.cfg) / "holdout.parquet")

    @property
    def evals(self) -> int:
        return Ledger(store_dir(self.cfg) / "ledger.jsonl").eval_count()


def make_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: str = "",
    data_extra: str = "",
    budget: int = 1,
    *,
    group: bool = False,
) -> Env:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    work = tmp_path / "w"
    (work / "data").mkdir(parents=True)
    rng = np.random.default_rng(3)
    frame = pd.DataFrame(
        {"x": rng.normal(size=100), "y": rng.integers(0, 2, size=100)},
    )
    if group:
        frame["g"] = np.arange(100) // 5
    frame.to_csv(work / "data" / "train.csv", index=False)
    (work / ".ml-referee.toml").write_text(
        '[project]\nid = "proj-p"\n\n[data]\ntrain = "data/train.csv"\nlabel = "y"\n'
        + ('group = "g"\n' if group else "")
        + data_extra
        + '\n[eval]\ncommand = "python eval.py"\nmetric = "acc"\nhigher_is_better = true\n'
        + extra
        + "\n[trials]\npatterns = []\n\n"
        f"[holdout]\nfraction = 0.25\nseed = 1\nbudget = {budget}\n",
    )
    cfg = load_config(work / ".ml-referee.toml")
    (work / "ev_src.py").write_text(EVALUATOR)
    seal(cfg, work / "ev_src.py")
    monkeypatch.chdir(work)
    return Env(work, cfg)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return make_env(tmp_path, monkeypatch)


@pytest.fixture
def made_dirs(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Record every temp directory the runner creates."""
    created: list[Path] = []
    original = tempfile.mkdtemp

    def spy(*args: object, **kwargs: object) -> str:
        path = original(*args, **kwargs)  # type: ignore[call-overload]
        created.append(Path(path))
        return str(path)

    monkeypatch.setattr(runner.tempfile, "mkdtemp", spy)
    return created


def test_happy_path_scores_and_records(env: Env, made_dirs: list[Path]) -> None:
    result = run_predict_eval(env.cfg, env.script(CONST))
    hold = env.holdout
    assert result.n == len(hold)
    assert result.value == pytest.approx(float((hold["y"] == 0).mean()))
    assert env.evals == 1
    records = Ledger(store_dir(env.cfg) / "ledger.jsonl").records()
    payload = records[-1].payload
    command = env.script(CONST)
    assert records[-1].kind == "eval"
    assert payload["predict_cmd_sha256"] == hashlib.sha256(command.encode()).hexdigest()
    assert "features" not in json.dumps(payload).lower()
    assert Ledger(store_dir(env.cfg) / "ledger.jsonl").verify().ok
    assert made_dirs
    assert not any(path.exists() for path in made_dirs)


def test_predictor_gets_unlabeled_features_in_the_workspace(env: Env) -> None:
    body = (
        "import os, shutil, sys\n"
        "shutil.copy(sys.argv[1], 'seen.csv')\n"
        "open('cwd.txt', 'w').write(os.getcwd())\n"
        "import pandas as pd\n"
        "n = len(pd.read_csv(sys.argv[1]))\n"
        "pd.DataFrame({'pred': [0] * n}).to_csv(sys.argv[2], index=False)\n"
    )
    run_predict_eval(env.cfg, env.script(body))
    seen = pd.read_csv(env.work / "seen.csv")
    hold = env.holdout
    assert "y" not in seen.columns
    assert list(seen.columns) == ["x"]
    assert len(seen) == len(hold)
    assert np.allclose(seen["x"].to_numpy(), hold["x"].to_numpy())
    assert (env.work / "cwd.txt").read_text() == str(env.work.resolve())
    # The features and the label never leave through a normal file name.
    assert "y" not in (env.work / "seen.csv").read_text().splitlines()[0].split(",")


def test_group_column_kept_by_default_and_hidden_on_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = "import shutil, sys\nshutil.copy(sys.argv[1], 'seen.csv')\n" + CONST.split("\n", 1)[1]
    kept = make_env(tmp_path / "a", monkeypatch, group=True)
    run_predict_eval(kept.cfg, kept.script(body))
    assert list(pd.read_csv(kept.work / "seen.csv").columns) == ["x", "g"]
    hidden = make_env(tmp_path / "b", monkeypatch, group=True, data_extra="group_hidden = true\n")
    run_predict_eval(hidden.cfg, hidden.script(body))
    assert list(pd.read_csv(hidden.work / "seen.csv").columns) == ["x"]


def test_budget_exhausted_before_any_features_are_written(
    env: Env, made_dirs: list[Path],
) -> None:
    run_predict_eval(env.cfg, env.script(CONST))
    made_dirs.clear()
    marker = env.work / "ran.txt"
    body = f"open({str(marker)!r}, 'w').write('x')\n" + CONST
    with pytest.raises(BudgetExhausted):
        run_predict_eval(env.cfg, env.script(body, "second.py"))
    assert not marker.exists()
    assert made_dirs == []
    assert env.evals == 1


@pytest.mark.parametrize(
    "body",
    [
        "import sys\nsys.exit(3)\n",
        "raise SystemExit('boom')\n",
        "pass\n",  # writes no output file
        CONST.replace("[0] * len(f)", "[0] * (len(f) - 1)"),  # too short
        CONST.replace("[0] * len(f)", "[0] * (len(f) + 1)"),  # too long
        CONST.replace("[0] * len(f)", "[float('nan')] * len(f)"),
        CONST.replace("[0] * len(f)", "[float('inf')] * len(f)"),
        CONST.replace("{'pred': [0] * len(f)}", "{'pred': [0] * len(f), 'b': [1] * len(f)}"),
        "import sys\nopen(sys.argv[2], 'w').write('not,a\\n\"csv')\n",
    ],
)
def test_bad_predictor_consumes_no_budget(
    env: Env, made_dirs: list[Path], body: str,
) -> None:
    with pytest.raises(PredictorError):
        run_predict_eval(env.cfg, env.script(body))
    assert env.evals == 0
    assert not any(path.exists() for path in made_dirs)
    # The budget is intact: a good predictor now succeeds.
    assert run_predict_eval(env.cfg, env.script(CONST, "good.py")).n == len(env.holdout)
    assert env.evals == 1


def test_predictor_timeout_kills_it_and_spends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, made_dirs: list[Path],
) -> None:
    env = make_env(tmp_path, monkeypatch, extra="predict_timeout_s = 1\n")
    marker = env.work / "late.txt"
    body = (
        "import time\ntime.sleep(2)\n"
        f"open({str(marker)!r}, 'w').write('x')\n"
    )
    started = time.monotonic()
    with pytest.raises(PredictorError, match="timed out"):
        run_predict_eval(env.cfg, env.script(body))
    assert time.monotonic() - started < 2
    time.sleep(2.2)
    assert not marker.exists()
    assert env.evals == 0
    assert not any(path.exists() for path in made_dirs)


def test_predictor_output_is_truncated_in_errors(env: Env) -> None:
    body = "import sys\nsys.stderr.write('E' * 200000)\nsys.exit(1)\n"
    with pytest.raises(PredictorError) as info:
        run_predict_eval(env.cfg, env.script(body))
    assert len(str(info.value)) < 5000


def test_command_must_name_features_and_out_and_parse(env: Env) -> None:
    for command in ("python x.py", "python x.py {features}", "python x.py {out}", "", "a 'b"):
        with pytest.raises(ValueError, match="predict"):
            run_predict_eval(env.cfg, command)
    assert env.evals == 0


def test_stray_background_process_cannot_change_scored_file(env: Env) -> None:
    # The scored file is a copy written after the predictor exits.
    marker = env.work / "bg.txt"
    body = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', "
        f"\"import time,sys; time.sleep(2); open({str(marker)!r},'w').write('x')\"])\n" + CONST
    )
    run_predict_eval(env.cfg, env.script(body))
    time.sleep(2.5)
    assert not marker.exists()


def test_failed_evaluator_after_good_predictions_spends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = make_env(tmp_path, monkeypatch)
    with pytest.raises(EvaluatorError):
        run_predict_eval(env.cfg, env.script(CONST.replace("'pred'", "'wrong'")))
    assert env.evals == 0


def test_cli_prints_only_score_and_n(
    env: Env, capsys: pytest.CaptureFixture[str],
) -> None:
    body = "import sys\nprint('predictor chatter')\nsys.stderr.write('stderr chatter')\n" + CONST
    code = main(["eval", "--predict-cmd", env.script(body)])
    out = capsys.readouterr()
    assert code == 0
    assert json.loads(out.out).keys() == {"metric", "value", "n"}
    assert "chatter" not in out.out + out.err
    assert env.evals == 1
    assert main(["verify-ledger"]) == 0


def test_cli_second_call_is_refused_without_running_the_predictor(
    env: Env, capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["eval", "--predict-cmd", env.script(CONST)]) == 0
    capsys.readouterr()
    marker = env.work / "ran.txt"
    body = f"open({str(marker)!r}, 'w').write('x')\n" + CONST
    assert main(["eval", "--predict-cmd", env.script(body, "again.py")]) == 1
    captured = capsys.readouterr()
    assert "budget" in captured.err.lower()
    assert not marker.exists()
    assert captured.out == ""


def test_cli_requires_exactly_one_source(env: Env) -> None:
    preds = env.work / "p.csv"
    preds.write_text("pred\n0\n")
    with pytest.raises(SystemExit):
        main(["eval"])
    with pytest.raises(SystemExit):
        main(["eval", "--predictions", str(preds), "--predict-cmd", "x {features} {out}"])


def test_cli_predictions_path_is_unchanged(
    env: Env, capsys: pytest.CaptureFixture[str],
) -> None:
    preds = env.work / "p.csv"
    pd.DataFrame({"pred": env.holdout["y"].to_numpy()}).to_csv(preds, index=False)
    assert main(["eval", "--predictions", str(preds)]) == 0
    assert json.loads(capsys.readouterr().out)["value"] == pytest.approx(1.0)
    last = Ledger(store_dir(env.cfg) / "ledger.jsonl").records()[-1]
    assert "predict_cmd_sha256" not in last.payload


def test_concurrent_predict_evals_spend_at_most_the_budget(
    env: Env,
) -> None:
    import multiprocessing as mp  # noqa: PLC0415

    command = env.script("import time\ntime.sleep(0.5)\n" + CONST)
    ctx = mp.get_context("fork")
    queue = ctx.Queue()

    def work() -> None:
        try:
            run_predict_eval(env.cfg, command)
            queue.put("ok")
        except BudgetExhausted:
            queue.put("budget")

    procs = [ctx.Process(target=work) for _ in range(3)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(30)
    results = sorted(queue.get(timeout=5) for _ in procs)
    assert results == ["budget", "budget", "ok"]
    assert env.evals == 1
    assert os.path.exists(store_dir(env.cfg) / "eval.lock")


def test_config_keys_validated(tmp_path: Path) -> None:
    base = (
        '[project]\nid = "c"\n\n[data]\ntrain = "t.csv"\nlabel = "y"\n{data}\n'
        '[eval]\ncommand = "x"\nmetric = "m"\nhigher_is_better = true\n{ev}\n'
        "[trials]\npatterns = []\n"
    )
    path = tmp_path / "c.toml"
    path.write_text(base.format(data="", ev=""))
    cfg = load_config(path)
    assert cfg.predict_timeout_s == 600
    assert cfg.group_hidden is False
    assert cfg.workspace == tmp_path
    path.write_text(base.format(data="group_hidden = true\n", ev="predict_timeout_s = 5\n"))
    cfg = load_config(path)
    assert (cfg.predict_timeout_s, cfg.group_hidden) == (5, True)
    for data, ev in (("group_hidden = 1\n", ""), ("", "predict_timeout_s = 0\n"), ("", "predict_timeout_s = true\n")):
        path.write_text(base.format(data=data, ev=ev))
        with pytest.raises(ConfigError):
            load_config(path)
