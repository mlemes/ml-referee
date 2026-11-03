"""Command-line interface for ml-referee."""

import argparse
import json
import os
import sys
from dataclasses import asdict, replace
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
from ml_referee.harness import HARNESSES
from ml_referee.install import install
from ml_referee.ledger import Ledger
from ml_referee.runner import _verify_store, run_holdout_eval
from ml_referee.seal import seal


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ml-referee")
    commands = parser.add_subparsers(dest="command", required=True)
    installer = commands.add_parser("install")
    installer.add_argument("--harness", choices=(*HARNESSES, "all"), required=True)
    installer.add_argument("--root", type=Path, default=Path.cwd())
    installer.add_argument("--dry-run", action="store_true")
    for name in ("seal", "eval", "audit", "claim", "verify-ledger"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path)
        if name == "seal":
            command.add_argument("--evaluator", type=Path, required=True)
            command.add_argument("--holdout-file", type=Path)
        if name in {"eval", "claim"}:
            command.add_argument("--predictions", type=Path, required=True)
        if name == "audit":
            command.add_argument("--sources", nargs="*", type=Path, default=[])
        if name in {"audit", "claim"}:
            command.add_argument("--against", nargs="*", type=Path, default=[])
        if name == "claim":
            command.add_argument("--val-scores", type=Path)
            command.add_argument("--p-value", type=float)
            command.add_argument("--out", type=Path)
    return parser


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.suffix == ".csv" else pd.read_parquet(path)


def _audit(
    cfg: Config, sources: list[Path], against: list[Path] | None = None,
) -> list[Finding]:
    """Run audits; counts reveal holdout membership (accepted residual)."""
    _verify_store(cfg, store_dir(cfg))
    train = _read(cfg.train_path)
    visible = [(path, _read(path)) for path in against or []]
    holdout = pd.read_parquet(store_dir(cfg) / "holdout.parquet")
    tagged = [
        replace(finding, detail=f"{finding.detail} [against={path.name}]")
        for path, frame in visible
        for finding in (
            *check_duplicates(train, frame.drop(columns=[cfg.label], errors="ignore")),
            *check_entity_overlap(
                train, frame.drop(columns=[cfg.label], errors="ignore"), cfg.group,
            ),
        )
    ]
    return [
        *check_duplicates(train, holdout.drop(columns=[cfg.label], errors="ignore")),
        *check_entity_overlap(train, holdout, cfg.group),
        *check_target_leak(train, cfg.label),
        *check_fit_on_full(sources),
        *tagged,
    ]


def _run(args: argparse.Namespace, cfg: Config) -> int:  # noqa: C901, PLR0912
    if args.command == "seal":
        manifest = seal(cfg, args.evaluator, args.holdout_file)
        sys.stdout.write(f"Sealed holdout rows: {manifest.n_holdout}\n")
    elif args.command == "eval":
        result = run_holdout_eval(cfg, args.predictions)
        sys.stdout.write(json.dumps(asdict(result)) + "\n")
    elif args.command == "audit":
        findings = _audit(cfg, args.sources, args.against)
        for finding in findings:
            sys.stdout.write(json.dumps(asdict(finding)) + "\n")
        return 3 if any(f.severity == "high" for f in findings) else 0
    elif args.command == "verify-ledger":
        verification = Ledger(store_dir(cfg) / "ledger.jsonl").verify()
        sys.stdout.write(json.dumps(asdict(verification)) + "\n")
        return 0 if verification.ok else 1
    elif args.command == "claim":
        if args.out is not None:
            output = args.out.resolve()
            directory = store_dir(cfg).resolve()
            if output == directory or directory in output.parents:
                msg = "Claim output must be outside the sealed store"
                raise ValueError(msg)
            if output.is_dir() or (output.exists() and not os.access(output, os.W_OK)):
                msg = "Claim output must be a writable file"
                raise ValueError(msg)
            if not output.parent.is_dir() or not os.access(output.parent, os.W_OK):
                msg = "Claim output parent must exist and be writable"
                raise ValueError(msg)
        scores = []
        if args.val_scores is not None:
            scores = json.loads(args.val_scores.read_text(encoding="utf-8"))
            if not isinstance(scores, list) or any(
                type(score) not in (int, float) for score in scores
            ):
                msg = "Validation scores must be a JSON list of numbers"
                raise ValueError(msg)
        findings = _audit(cfg, [], args.against)
        report = claim(cfg, args.predictions, scores, args.p_value, findings=findings)
        if args.out is not None:
            args.out.write_text(report, encoding="utf-8")
        else:
            sys.stdout.write(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the ml-referee CLI and return the process exit code."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "install":
            targets = HARNESSES if args.harness == "all" else (args.harness,)
            for name in targets:
                for change in install(name, args.root, dry_run=args.dry_run):
                    sys.stdout.write(f"{change.action}: {change.path}\n")
            return 0
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
