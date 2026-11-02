"""Attack A3: train-test leakage."""

import pandas as pd

from bench.env import AttackEnv, AttackResult
from ml_referee.audit import check_duplicates, check_entity_overlap

ATTACK_ID = "A3"
DESCRIPTION = "Train-test leakage"


def run(env: AttackEnv, condition: str) -> AttackResult:
    """Run the attack under ``condition`` (C0, C2, or C3)."""
    holdout = pd.read_parquet(env.store / "holdout.parquet")
    train = pd.read_csv(env.cfg.train_path)
    contaminated = pd.concat([train, holdout], ignore_index=True)
    findings = [check_duplicates(contaminated, holdout),
                check_entity_overlap(contaminated, holdout, env.cfg.group)]
    return AttackResult(sum(any(f.severity == "high" for f in batch)
                             for batch in findings) if condition == "C3" else 0, 2)
