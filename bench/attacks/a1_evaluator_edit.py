"""Attack A1: evaluator tampering."""

from bench.env import AttackEnv, AttackResult

ATTACK_ID = "A1"
DESCRIPTION = "Evaluator tampering"


def run(env: AttackEnv, condition: str) -> AttackResult:
    """Run the attack under ``condition`` (C0, C2, or C3)."""
    raise NotImplementedError
