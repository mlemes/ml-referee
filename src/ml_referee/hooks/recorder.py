"""PostToolUse hook that records trial commands in the ledger."""

from ml_referee.config import Config
from ml_referee.ledger import Ledger, Record


def record(event: dict[str, object], cfg: Config, ledger: Ledger) -> Record | None:
    """Append a trial record when the event is a matching Bash command."""
    raise NotImplementedError


def main() -> int:
    """Read one hook event from stdin and record it. Never raises."""
    raise NotImplementedError
