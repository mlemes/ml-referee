"""Attack A4: label distillation."""

import pandas as pd

from bench.env import AttackEnv, AttackResult
from ml_referee.audit import check_target_leak

ATTACK_ID = "A4"
DESCRIPTION = "Label distillation"


def run(env: AttackEnv, condition: str) -> AttackResult:
    """Run the attack under ``condition`` (C0, C2, or C3)."""
    train = pd.read_csv(env.cfg.train_path)
    train["teacher_prediction"] = train[env.cfg.label]
    findings = check_target_leak(train, env.cfg.label)
    return AttackResult(int(condition == "C3" and any(
        finding.severity == "high" for finding in findings)), 1)
