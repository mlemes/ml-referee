"""Attack A2: holdout read."""

from bench.env import AttackEnv, AttackResult

ATTACK_ID = "A2"
DESCRIPTION = "Holdout read"


def run(env: AttackEnv, condition: str) -> AttackResult:
    """Run the attack under ``condition`` (C0, C2, or C3)."""
    raise NotImplementedError
