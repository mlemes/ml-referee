"""Render and apply the ml-referee block in AGENTS.md."""

from pathlib import Path

from ml_referee.install import Change

START = "<!-- ml-referee:start -->"
END = "<!-- ml-referee:end -->"

_BODY = """\
## ml-referee

This project's holdout data is sealed by ml-referee.

- Never read, copy, or reference the sealed store (holdout data, evaluator,
  manifest, or ledger) or the `.ml-referee.toml` config, directly or through
  shell commands, scripts, or search.
- Do not try to work around the hooks that enforce this.
- When a sealed evaluation is needed, ask the person to run `ml-referee claim`.
"""


def render_block() -> str:
    """Return the AGENTS.md block, including both markers."""
    return f"{START}\n{_BODY}{END}\n"


def apply_block(path: Path, *, dry_run: bool = False) -> Change:
    """Create or update the block in the file at path."""
    block = render_block()
    if not path.exists():
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(block, encoding="utf-8")
        return Change(path, "create")
    text = path.read_text(encoding="utf-8")
    start = text.find(START)
    end = text.find(END)
    if text.count(START) > 1 or text.count(END) > 1 or (start == -1) != (end == -1):
        msg = f"{path} has unmatched or duplicate ml-referee markers"
        raise ValueError(msg)
    if start != -1 and end < start:
        msg = f"{path} has ml-referee markers in the wrong order"
        raise ValueError(msg)
    if start != -1:
        tail = end + len(END)
        if text[tail : tail + 1] == "\n":
            tail += 1
        new = text[:start] + block + text[tail:]
    else:
        sep = ""
        if text and not text.endswith("\n\n"):
            sep = "\n" if text.endswith("\n") else "\n\n"
        new = text + sep + block
    if new == text:
        return Change(path, "unchanged")
    if not dry_run:
        path.write_text(new, encoding="utf-8")
    return Change(path, "merge")
