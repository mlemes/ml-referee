"""Attack A5: holdout reuse."""

from bench.env import AttackEnv, AttackResult

ATTACK_ID = "A5"
DESCRIPTION = "Holdout reuse"


def run(env: AttackEnv, condition: str) -> AttackResult:
    """Run the attack under ``condition`` (C0, C2, or C3)."""
    raise NotImplementedError
