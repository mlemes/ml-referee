"""Attack A3: train-test leakage."""

from bench.env import AttackEnv, AttackResult

ATTACK_ID = "A3"
DESCRIPTION = "Train-test leakage"


def run(env: AttackEnv, condition: str) -> AttackResult:
    """Run the attack under ``condition`` (C0, C2, or C3)."""
    raise NotImplementedError
