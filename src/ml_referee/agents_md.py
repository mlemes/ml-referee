"""Render and apply the ml-referee block in AGENTS.md."""

from pathlib import Path

from ml_referee.install import Change

START = "<!-- ml-referee:start -->"
END = "<!-- ml-referee:end -->"

_HEAD = """\
## ml-referee

This project's holdout data is sealed by ml-referee.

- Never read, list, copy, or reference the sealed store (holdout data,
  evaluator, manifest, or ledger), and never edit the `.ml-referee.toml`
  config, directly or through shell commands, scripts, or search.
- Do not try to work around the hooks that enforce this.
- Commands you may run, alone or in ordinary pipelines and redirections:
  - `ml-referee audit [--against PATH ...] [--sources PATH ...]` checks the
    training data, the files you pass, and your sources for leakage, and
    prints one JSON finding per line.
  - `ml-referee eval --predictions PATH` scores a predictions file for the
    sealed rows. You cannot see those rows, so this needs a file made by
    someone who can.
  - `ml-referee eval --predict-cmd "COMMAND"` runs COMMAND in the workspace
    with `{features}` (a CSV of the sealed rows without labels) and `{out}`
    (a one-column predictions CSV for it to write), then scores the result.
    It prints only the score and the row count.
  - `ml-referee verify-ledger` checks that the evaluation ledger is intact.
- Sealed evaluations are limited by the holdout budget. A failed predict
  command spends none; each scored evaluation spends one.
"""
_BUDGET = (
    "- The holdout budget for this project is {budget} sealed "
    "evaluation{plural} in total.\n"
)
_TAIL = """\
- The person produces the final claim with `ml-referee claim`. `ml-referee seal`,
  `ml-referee claim`, and `ml-referee install` are not for the agent.
"""


def render_block(budget: int | None = None) -> str:
    """Return the AGENTS.md block, including both markers."""
    note = (
        _BUDGET.format(budget=budget, plural="" if budget == 1 else "s")
        if budget is not None
        else ""
    )
    return f"{START}\n{_HEAD}{note}{_TAIL}{END}\n"


def apply_block(
    path: Path, *, dry_run: bool = False, budget: int | None = None,
) -> Change:
    """Create or update the block in the file at path."""
    block = render_block(budget)
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
