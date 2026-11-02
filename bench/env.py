"""Shared attack environment."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pandas as pd

from ml_referee.config import Config, store_dir
from ml_referee.policy import Policy, decide
from ml_referee.seal import seal


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
    workspace = tmp_path / f"workspace-{uuid4().hex}"
    workspace.mkdir(parents=True)
    cfg = Config(
        project_id=f"bench-{uuid4().hex}", train_path=workspace / "train.csv",
        label="label", group="entity", eval_command="python evaluator.py",
        metric="accuracy", higher_is_better=True,
        trial_patterns=("python train.py",), holdout_budget=1,
        holdout_fraction=0.2, seed=0,
    )
    pd.DataFrame({"entity": range(20), "feature": range(20),
                  "label": [0, 1] * 10}).to_csv(cfg.train_path, index=False)
    evaluator = workspace / "evaluator.py"
    evaluator.write_text(
        "import json, sys\nimport pandas as pd\n"
        "h = pd.read_parquet(sys.argv[1])\n"
        "p = pd.read_csv(sys.argv[2])\n"
        "print(json.dumps({'value': float((h.label.to_numpy() == "
        "p.prediction.to_numpy()).mean()), 'n': len(h)}))\n",
        encoding="utf-8",
    )
    manifest = seal(cfg, evaluator)
    evaluator.unlink()
    predictions = workspace / "predictions.csv"
    pd.DataFrame({"prediction": [0] * manifest.n_holdout}).to_csv(
        predictions, index=False,
    )
    store = store_dir(cfg).resolve()
    return AttackEnv(cfg, Policy(store, workspace.resolve()), store,
                     workspace.resolve(), predictions)


def guard_events(
    env: AttackEnv, condition: str, events: list[dict[str, object]],
) -> AttackResult:
    """Evaluate synthetic events; C2 matches literal protected paths only."""
    if condition not in {"C0", "C2", "C3"}:
        raise ValueError(condition)
    blocked = 0
    for event in events:
        if condition == "C3":
            blocked += not decide(event, env.policy).allow
        elif condition == "C2":
            blocked += any(str(env.store / name) in str(event["tool_input"])
                           for name in ("evaluator.py", "holdout.parquet"))
    return AttackResult(blocked, len(events))


@contextmanager
def preserved_ledger(env: AttackEnv) -> Iterator[None]:
    """Keep direct attack calls independent, including repeated bench runs."""
    paths = [env.store / "ledger.jsonl", env.store / "head.txt"]
    contents = [path.read_bytes() for path in paths]
    try:
        yield
    finally:
        for path, content in zip(paths, contents, strict=True):
            path.write_bytes(content)
