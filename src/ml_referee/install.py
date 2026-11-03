"""Install ml-referee hooks into agent harness configuration files."""

from dataclasses import dataclass
from pathlib import Path

OPENCODE_PLUGIN_SRC: Path = (
    Path(__file__).resolve().parents[2] / "integrations" / "opencode" / "ml-referee.js"
)


@dataclass(frozen=True)
class Change:
    """One file change made (or planned) by the installer."""

    path: Path
    action: str


def install(
    harness: str,
    root: Path,
    *,
    dry_run: bool = False,
    python: str | None = None,
) -> list[Change]:
    """Install hooks for one harness under root and report the changes."""
    raise NotImplementedError
