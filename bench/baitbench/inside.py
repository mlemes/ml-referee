"""Container-side entry point: ``python -m bench.baitbench.inside --stage ...``.

Stages run in separate containers that share /work (workspace), /state (the
sealed store through XDG_DATA_HOME) and /out (the run directory). Only the
setup and finalize stages see the hidden file at /hidden/robust_test.csv.
"""

import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from bench.baitbench.c3 import (
    TRIAL_COMMAND,
    c3_config,
    evaluator_source,
    parse_claim,
    trial_event,
)
from bench.baitbench.score import score_workspace
from bench.baitbench.scripted import agent_steps, solution_source

WORK = Path("/work")
OUT = Path("/out")
HIDDEN = Path("/hidden/robust_test.csv")
TIMEOUT_S = 900


def _run(argv: list[str], *, ok: tuple[int, ...] = (0,), stdin: str | None = None) -> str:
    run = subprocess.run(  # noqa: S603
        argv, cwd=WORK, input=stdin, capture_output=True, text=True,
        check=False, timeout=TIMEOUT_S,
    )
    if run.returncode not in ok:
        msg = f"{' '.join(argv[:3])} exited {run.returncode}: {run.stderr.strip()[-500:]}"
        raise RuntimeError(msg)
    return run.stdout


def setup() -> None:
    """Write the config, seal the hidden split, and install the hooks (C3 only)."""
    task = json.loads((WORK / "task.json").read_text(encoding="utf-8"))
    with (WORK / "train.csv").open(encoding="utf-8") as stream:
        header = next(csv.reader(stream))
    family = task["task_id"].rsplit("_n", 1)[0]
    (WORK / ".ml-referee.toml").write_text(
        c3_config(task, family, has_entity="entity_id" in header), encoding="utf-8",
    )
    with tempfile.TemporaryDirectory(prefix="stage-") as stage:
        evaluator = Path(stage) / "evaluator.py"
        evaluator.write_text(evaluator_source(task["metric"], task["target_column"]))
        staged = Path(stage) / "robust_test.csv"
        shutil.copyfile(HIDDEN, staged)
        try:
            _run(["ml-referee", "seal", "--evaluator", str(evaluator), "--holdout-file", str(staged)])
        finally:
            staged.unlink(missing_ok=True)
    _run(["ml-referee", "install", "--harness", "claude", "--root", str(WORK)])


def agent(name: str, condition: str) -> None:
    """Run the scripted agent: write each solution version, evaluate, record trials."""
    task = json.loads((WORK / "task.json").read_text(encoding="utf-8"))
    family = task["task_id"].rsplit("_n", 1)[0]
    scores = []
    for kind, alpha in agent_steps(name, family):
        (WORK / "solution.py").write_text(
            solution_source(kind, task["task_type"], alpha), encoding="utf-8",
        )
        with (WORK / "run.log").open("w", encoding="utf-8") as log:
            run = subprocess.run(
                [sys.executable, "evaluate.py"], cwd=WORK, stdout=log,
                stderr=subprocess.STDOUT, check=False, timeout=TIMEOUT_S,
            )
        if run.returncode != 0:
            msg = f"evaluate.py failed for {kind}: {(WORK / 'run.log').read_text()[-500:]}"
            raise RuntimeError(msg)
        scores.append(float(json.loads((WORK / "metrics.json").read_text())["score"]))
        if condition == "C3":
            event = trial_event(TRIAL_COMMAND, str(WORK), exit_code=run.returncode)
            _run(
                [sys.executable, "-m", "ml_referee.hooks.recorder", "--harness", "claude"],
                stdin=json.dumps(event),
            )
    (OUT / "agent.json").write_text(json.dumps({"scores": scores}), encoding="utf-8")


def finalize(condition: str) -> None:
    """Score the final solution and, for C3, audit and claim."""
    predictions = Path(tempfile.mkdtemp(prefix="pred-")) / "hidden_predictions.csv"
    result = score_workspace(WORK, HIDDEN, predictions)
    result["audit_findings"] = []
    result["sealed_score"] = None
    result["trial_count"] = None
    if condition == "C3":
        scores = json.loads((OUT / "agent.json").read_text())["scores"]
        val = predictions.with_name("val.json")
        val.write_text(json.dumps(scores), encoding="utf-8")
        audit = _run(
            ["ml-referee", "audit", "--sources", str(WORK / "solution.py"),
             "--against", str(WORK / "test.csv")],
            ok=(0, 3),
        )
        result["audit_findings"] = [json.loads(x) for x in audit.splitlines() if x.strip()]
        _run(["ml-referee", "claim", "--predictions", str(predictions), "--against",
              str(WORK / "test.csv"), "--val-scores", str(val), "--out", str(OUT / "claim.md")])
        parsed = parse_claim((OUT / "claim.md").read_text(encoding="utf-8"))
        result["sealed_score"] = parsed["sealed_score"]
        result["trial_count"] = parsed["trials"]
    (OUT / "inside.json").write_text(json.dumps(result), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Dispatch one stage."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("setup", "agent", "finalize"), required=True)
    parser.add_argument("--condition", choices=("C0", "C3"), required=True)
    parser.add_argument("--agent", default="")
    args = parser.parse_args(argv)
    if args.stage == "setup":
        setup()
    elif args.stage == "agent":
        agent(args.agent, args.condition)
    else:
        finalize(args.condition)
    return 0


if __name__ == "__main__":
    sys.exit(main())
