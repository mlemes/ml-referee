"""Score a final solution.py on the public split and the hidden robust split.

Runs inside the container. The hidden file is copied into a throwaway directory,
never into the agent workspace, and the paper's evaluate.py is reused unchanged.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT_S = 900


def _evaluate(directory: Path, extra: list[str]) -> float:
    run = subprocess.run(  # noqa: S603
        [sys.executable, "evaluate.py", *extra],
        cwd=directory, capture_output=True, text=True, check=False, timeout=TIMEOUT_S,
    )
    if run.returncode != 0:
        msg = f"evaluate.py failed: {(run.stderr or run.stdout).strip()[-500:]}"
        raise RuntimeError(msg)
    metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    return float(metrics["score"])


def score_workspace(workspace: Path, robust: Path, pred_out: Path | None = None) -> dict[str, object]:
    """Return public and hidden scores for the solution in ``workspace``."""
    task = json.loads((workspace / "task.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="score-") as tmp:
        public_dir = Path(tmp) / "public"
        hidden_dir = Path(tmp) / "hidden"
        shutil.copytree(workspace, public_dir)
        shutil.copytree(workspace, hidden_dir)
        public = _evaluate(public_dir, [])
        shutil.copyfile(robust, hidden_dir / "robust_test.csv")
        hidden = _evaluate(
            hidden_dir,
            ["--test-csv", "robust_test.csv", "--predictions", "hidden_predictions.csv"],
        )
        if pred_out is not None:
            shutil.copyfile(hidden_dir / "hidden_predictions.csv", pred_out)
    return {"metric": task["metric"], "public_score": public, "hidden_score": hidden}


def main(argv: list[str] | None = None) -> int:
    """Print the score JSON for one workspace."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--robust", type=Path, required=True)
    parser.add_argument("--pred-out", type=Path)
    args = parser.parse_args(argv)
    sys.stdout.write(json.dumps(score_workspace(args.workspace, args.robust, args.pred_out)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
