"""Hash-chained append-only ledger."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Record:
    """Record type."""

    index: int
    kind: str
    payload: dict[str, object]
    time: str
    prev_hash: str
    hash: str


@dataclass(frozen=True)
class VerifyResult:
    """VerifyResult type."""

    ok: bool
    reason: str


class Ledger:
    """Hash-chained ledger."""

    def __init__(self, path: Path) -> None:
        """Open a ledger at path."""
        self.path = path

    def append(self, kind: str, payload: dict[str, object]) -> Record:
        """Append."""
        raise NotImplementedError

    def records(self) -> list[Record]:
        """Return all records."""
        raise NotImplementedError

    def verify(self) -> VerifyResult:
        """Verify."""
        raise NotImplementedError

    def trial_count(self) -> int:
        """trial_count."""
        raise NotImplementedError

    def eval_count(self) -> int:
        """eval_count."""
        raise NotImplementedError
