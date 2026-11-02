"""Decide whether a tool call may touch the sealed store."""

from dataclasses import dataclass
from pathlib import Path

from ml_referee.config import Config


@dataclass(frozen=True)
class Policy:
    """Sealed store and workspace locations."""

    store: Path
    workspace: Path

    def touches_sealed(self, path_or_command: str) -> bool:
        """Return True if the path or shell command may reach the store."""
        raise NotImplementedError


@dataclass(frozen=True)
class Decision:
    """Allow or deny verdict with a reason."""

    allow: bool
    reason: str


def build_policy(cfg: Config, workspace: Path) -> Policy:
    """Build a policy for ``cfg`` and ``workspace``."""
    raise NotImplementedError


def decide(event: dict[str, object], policy: Policy) -> Decision:
    """Decide on one PreToolUse event."""
    raise NotImplementedError
