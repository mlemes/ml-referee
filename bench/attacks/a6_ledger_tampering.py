"""Attack A6: ledger tampering."""

import json

from bench.env import AttackEnv, AttackResult, preserved_ledger
from ml_referee.ledger import Ledger

ATTACK_ID = "A6"
DESCRIPTION = "Ledger tampering"


def run(env: AttackEnv, condition: str) -> AttackResult:
    """Run the attack under ``condition`` (C0, C2, or C3)."""
    with preserved_ledger(env):
        ledger = Ledger(env.store / "ledger.jsonl")
        records = ledger.path.read_text(encoding="utf-8").splitlines()
        data = json.loads(records[0])
        data["payload"]["metric"] = "fabricated"
        records[0] = json.dumps(data)
        ledger.path.write_text("\n".join(records) + "\n", encoding="utf-8")
        detected = not ledger.verify().ok
    return AttackResult(int(condition == "C3" and detected), 1)
