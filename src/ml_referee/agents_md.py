"""Render and apply the ml-referee block in AGENTS.md."""

from pathlib import Path

from ml_referee.install import Change

START = "<!-- ml-referee:start -->"
END = "<!-- ml-referee:end -->"


def render_block() -> str:
    """Return the AGENTS.md block, including both markers."""
    raise NotImplementedError


def apply_block(path: Path) -> Change:
    """Create or update the block in the file at path."""
    raise NotImplementedError
