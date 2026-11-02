"""Attack A5: holdout reuse."""

from bench.env import AttackEnv, AttackResult, preserved_ledger
from ml_referee.ledger import Ledger
from ml_referee.runner import BudgetExhausted, run_holdout_eval

ATTACK_ID = "A5"
DESCRIPTION = "Holdout reuse"


def run(env: AttackEnv, condition: str) -> AttackResult:
    """Run the attack under ``condition`` (C0, C2, or C3)."""
    attempts = env.cfg.holdout_budget + 2
    if condition != "C3":
        return AttackResult(0, attempts)
    blocked = 0
    with preserved_ledger(env):
        # Legitimate evaluations exhaust the budget before the reuse attack starts.
        ledger = Ledger(env.store / "ledger.jsonl")
        while ledger.eval_count() < env.cfg.holdout_budget:
            run_holdout_eval(env.cfg, env.predictions)
        for _ in range(attempts):
            try:
                run_holdout_eval(env.cfg, env.predictions)
            except BudgetExhausted:
                blocked += 1
    return AttackResult(blocked, attempts)
