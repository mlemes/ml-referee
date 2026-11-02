"""Run every scripted attack under each condition."""

from bench.env import AttackEnv
from ml_referee.policy import Policy


def run_all(
    policy_or_env: Policy | AttackEnv,
    conditions: tuple[str, ...] = ("C0", "C2", "C3"),
) -> dict[str, dict[str, float]]:
    """Return the block rate per attack id and condition."""
    raise NotImplementedError


def main() -> int:
    """Print a Markdown table of block rates and return 0."""
    raise NotImplementedError
