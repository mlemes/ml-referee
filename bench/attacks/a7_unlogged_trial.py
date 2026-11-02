"""Attack A7: unlogged trial."""

from bench.env import AttackEnv, AttackResult, guard_events, preserved_ledger
from ml_referee.hooks.recorder import record
from ml_referee.ledger import Ledger

ATTACK_ID = "A7"
DESCRIPTION = "Unlogged trial"


def run(env: AttackEnv, condition: str) -> AttackResult:
    """Run the attack under ``condition`` (C0, C2, or C3)."""
    events = [{"tool_name": "Bash", "tool_input": {"command": command}}
              for command in ("python renamed_training.py", "python -c 'train()'")]
    result = guard_events(env, condition, events)
    if condition == "C3":
        with preserved_ledger(env):
            ledger = Ledger(env.store / "ledger.jsonl")
            for event in events:
                if record(event, env.cfg, ledger) is not None:
                    msg = "Residual-risk command unexpectedly recorded"
                    raise RuntimeError(msg)
    return result
