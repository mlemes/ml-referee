"""Attack A7: unlogged trial."""

from bench.env import AttackEnv, AttackResult

ATTACK_ID = "A7"
DESCRIPTION = "Unlogged trial"


def run(env: AttackEnv, condition: str) -> AttackResult:
    """Run the attack under ``condition`` (C0, C2, or C3)."""
    raise NotImplementedError
