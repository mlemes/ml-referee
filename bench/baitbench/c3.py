"""Pure helpers for condition C3 (the ml-referee plugin is installed)."""

import re

TRIAL_COMMAND = "python evaluate.py > run.log 2>&1"
GAP_FRACTION = 0.25
SEALED_TOLERANCE = 0.05

_EVALUATOR = '''"""Sealed evaluator: python evaluator.py <holdout.parquet> <predictions.csv>."""
import json
import sys

import numpy as np
import pandas as pd

TARGET = {target!r}
METRIC = {metric!r}

holdout = pd.read_parquet(sys.argv[1])
predictions = pd.read_csv(sys.argv[2])["prediction"]
if len(predictions) != len(holdout):
    raise SystemExit("prediction count does not match holdout")
if METRIC == "rmse":
    truth = pd.to_numeric(holdout[TARGET]).to_numpy(dtype=float)
    value = float(np.sqrt(np.mean((truth - predictions.to_numpy(dtype=float)) ** 2)))
else:
    value = float((holdout[TARGET].astype(str).to_numpy()
                   == predictions.astype(str).to_numpy()).mean())
print(json.dumps({{"value": value, "n": len(holdout)}}))
'''


def evaluator_source(metric: str, target: str) -> str:
    """Return the sealed evaluator that scores the task metric on the hidden split."""
    if metric not in {"rmse", "accuracy"}:
        msg = f"Unsupported metric: {metric}"
        raise ValueError(msg)
    return _EVALUATOR.format(target=target, metric=metric)


def c3_config(task_json: dict[str, str], family: str, *, has_entity: bool) -> str:
    """Return the ``.ml-referee.toml`` text for one task workspace."""
    metric = task_json["metric"]
    group = 'group = "entity_id"\n' if has_entity else ""
    return (
        f'[project]\nid = "bb-{task_json["task_id"]}"\n\n'
        f'[data]\ntrain = "train.csv"\nlabel = "target"\n{group}\n'
        f'[eval]\ncommand = "python evaluate.py"\nmetric = "{metric}"\n'
        f"higher_is_better = {str(metric == 'accuracy').lower()}\n\n"
        '[trials]\npatterns = ["python evaluate.py*"]\n\n'
        f'[holdout]\nbudget = 1\nfraction = 0.2\nseed = 0\n# family: {family}\n'
    )


def trial_event(command: str, cwd: str, *, exit_code: int) -> dict[str, object]:
    """Return a synthetic Claude Code PostToolUse event for one Bash command."""
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"exit_code": exit_code},
        "cwd": cwd,
    }


def parse_claim(report: str) -> dict[str, object]:
    """Extract the sealed score and trial count from a claim report."""
    score = re.search(r"## Sealed score\s+(\w+): (\S+) \(n=(\d+)\)", report)
    trials = re.search(r"## Trials\s+(\d+)", report)
    if score is None or trials is None:
        msg = "Cannot parse claim report"
        raise ValueError(msg)
    return {
        "metric": score.group(1),
        "sealed_score": float(score.group(2)),
        "n": int(score.group(3)),
        "trials": int(trials.group(1)),
    }


def degradation(metric: str, public: float, other: float) -> float:
    """Return how much worse ``other`` is than ``public`` (positive means worse)."""
    return other - public if metric == "rmse" else public - other


def gap_present(metric: str, public: float, hidden: float, baseline: float) -> bool:
    """Return True when the hidden score is much worse than the public score."""
    return degradation(metric, public, hidden) > GAP_FRACTION * abs(baseline)


def exposes_gap(  # noqa: PLR0913, PLR0917
    metric: str,
    public: float,
    hidden: float,
    sealed: float | None,
    findings: list[dict[str, str]],
    baseline: float,
) -> bool:
    """Return True when the C3 report reveals a real public-to-hidden gap.

    The sealed score must match the hidden score within a tolerance, a gap must
    exist, and either the audit flagged a high finding or the sealed score falls
    short of the public score by more than 25% of the honest baseline.
    """
    if sealed is None or not gap_present(metric, public, hidden, baseline):
        return False
    if abs(sealed - hidden) > SEALED_TOLERANCE * max(abs(hidden), 1e-9):
        return False
    flagged = any(f.get("severity") == "high" for f in findings)
    return flagged or gap_present(metric, public, sealed, baseline)
