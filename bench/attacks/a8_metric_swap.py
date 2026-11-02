"""Attack A8: metric substitution."""

from bench.env import AttackEnv, AttackResult

ATTACK_ID = "A8"
DESCRIPTION = "Metric substitution"


def run(env: AttackEnv, condition: str) -> AttackResult:
    """Run the attack under ``condition`` (C0, C2, or C3)."""
    raise NotImplementedError
