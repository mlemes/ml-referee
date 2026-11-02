"""Command-line interface for ml-referee."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from ml_referee.audit import (
    Finding,
    check_duplicates,
    check_entity_overlap,
    check_fit_on_full,
    check_target_leak,
)
from ml_referee.claim import claim
from ml_referee.config import Config, find_config, load_config, store_dir
from ml_referee.ledger import Ledger
from ml_referee.runner import _verify_store, run_holdout_eval
from ml_referee.seal import seal


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ml-referee")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("seal", "eval", "audit", "claim", "verify-ledger"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path)
        if name == "seal":
            command.add_argument("--evaluator", type=Path, required=True)
        if name in {"eval", "claim"}:
            command.add_argument("--predictions", type=Path, required=True)
        if name == "audit":
            command.add_argument("--sources", nargs="*", type=Path, default=[])
        if name == "claim":
            command.add_argument("--val-scores", type=Path)
            command.add_argument("--p-value", type=float)
            command.add_argument("--out", type=Path)
    return parser


def _audit(cfg: Config, sources: list[Path]) -> list[Finding]:
    _verify_store(cfg, store_dir(cfg))
    train = (
        pd.read_csv(cfg.train_path)
        if cfg.train_path.suffix == ".csv"
        else pd.read_parquet(cfg.train_path)
    )
    holdout = pd.read_parquet(store_dir(cfg) / "holdout.parquet")
    return [
        *check_duplicates(train, holdout),
        *check_entity_overlap(train, holdout, cfg.group),
        *check_target_leak(train, cfg.label),
        *check_fit_on_full(sources),
    ]


def _run(args: argparse.Namespace, cfg: Config) -> int:
    if args.command == "seal":
        manifest = seal(cfg, args.evaluator)
        sys.stdout.write(f"Sealed holdout rows: {manifest.n_holdout}\n")
    elif args.command == "eval":
        result = run_holdout_eval(cfg, args.predictions)
        sys.stdout.write(json.dumps(asdict(result)) + "\n")
    elif args.command == "audit":
        findings = _audit(cfg, args.sources)
        for finding in findings:
            sys.stdout.write(json.dumps(asdict(finding)) + "\n")
        return 3 if any(f.severity == "high" for f in findings) else 0
    elif args.command == "verify-ledger":
        verification = Ledger(store_dir(cfg) / "ledger.jsonl").verify()
        sys.stdout.write(json.dumps(asdict(verification)) + "\n")
        return 0 if verification.ok else 1
    elif args.command == "claim":
        scores = []
        if args.val_scores is not None:
            scores = json.loads(args.val_scores.read_text(encoding="utf-8"))
            if not isinstance(scores, list) or any(
                type(score) not in (int, float) for score in scores
            ):
                msg = "Validation scores must be a JSON list of numbers"
                raise ValueError(msg)
        report = claim(
            cfg, args.predictions, scores, args.p_value, findings=_audit(cfg, []),
        )
        if args.out is not None:
            args.out.write_text(report, encoding="utf-8")
        else:
            sys.stdout.write(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the ml-referee CLI and return the process exit code."""
    args = _parser().parse_args(argv)
    try:
        config = args.config or find_config(Path.cwd())
        if config is None:
            sys.stderr.write("ml-referee: No .ml-referee.toml found\n")
            return 1
        return _run(args, load_config(config.resolve()))
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        sys.stderr.write(f"ml-referee: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
