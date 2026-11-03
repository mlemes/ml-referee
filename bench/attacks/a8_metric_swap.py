"""Attack A8: metric substitution."""

from dataclasses import replace

from bench.env import AttackEnv, AttackResult, preserved_ledger
from ml_referee.claim import claim

ATTACK_ID = "A8"
DESCRIPTION = "Metric substitution"


def run(env: AttackEnv, condition: str) -> AttackResult:
    """Run the attack under ``condition`` (C0, C2, or C3)."""
    if condition != "C3":
        return AttackResult(0, 1)
    with preserved_ledger(env):
        report = claim(replace(env.cfg, metric="fabricated"), env.predictions, [])
    # The report must retain the sealed metric despite workspace config changes.
    retained = f"{env.cfg.metric}:" in report and "fabricated:" not in report
    return AttackResult(int(retained), 1)
