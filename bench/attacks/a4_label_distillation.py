"""Attack A4: label distillation."""

from bench.env import AttackEnv, AttackResult

ATTACK_ID = "A4"
DESCRIPTION = "Label distillation"


def run(env: AttackEnv, condition: str) -> AttackResult:
    """Run the attack under ``condition`` (C0, C2, or C3)."""
    raise NotImplementedError
