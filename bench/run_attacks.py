"""Run every scripted attack under each condition."""

import os
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory

from bench.env import AttackEnv, make_env
from ml_referee.policy import Policy


def run_all(
    policy_or_env: Policy | AttackEnv,
    conditions: tuple[str, ...] = ("C0", "C2", "C3"),
) -> dict[str, dict[str, float]]:
    """Return the block rate per attack id and condition."""
    if any(condition not in {"C0", "C2", "C3"} for condition in conditions):
        msg = "Scripted conditions must be C0, C2, or C3"
        raise ValueError(msg)
    if isinstance(policy_or_env, Policy):
        msg = "Direct module attacks require AttackEnv; use make_env()"
        raise TypeError(msg)
    modules = sorted((Path(__file__).parent / "attacks").glob("a[1-8]_*.py"))
    table = {}
    for path in modules:
        attack = import_module(f"bench.attacks.{path.stem}")
        rates = {}
        for condition in conditions:
            result = attack.run(policy_or_env, condition)
            rates[condition] = result.blocked / result.attempted
        table[attack.ATTACK_ID] = rates
    return table


def main() -> int:
    """Print a Markdown table of block rates and return 0."""
    with TemporaryDirectory(prefix="referee-bench-") as directory:
        root = Path(directory)
        previous = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = str(root / "xdg")
        try:
            table = run_all(make_env(root))
        finally:
            if previous is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = previous
    print("| Attack | C0 | C2 | C3 |")  # noqa: T201
    print("| --- | --- | --- | --- |")  # noqa: T201
    for attack, rates in table.items():
        values = " | ".join(f"{rate:.2f}" for rate in rates.values())
        print(f"| {attack} | {values} |")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
