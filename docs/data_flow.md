# Data flow

This document traces data through `ml-referee`. It describes the planned design. Update it in the same change as the code.

## Seal

1. You run `/ml-seal`. The command calls `cli.seal`.
2. `config.load_config` reads `.ml-referee.toml` (TOML, trusted: written by you).
3. `seal.seal` reads the training file named in the config (Parquet or CSV, trusted).
4. `seal.seal` splits rows by the `group` column when present, using the seed.
5. `seal.seal` writes the holdout, the evaluator, and `manifest.json` to the sealed store. The manifest holds SHA-256 hashes and the seed.
6. `seal.seal` writes the training and validation splits back to the workspace. The holdout never returns to the workspace.

## Guard

1. Claude Code sends a tool call to `hooks.guard` as JSON on standard input (untrusted: the agent chose it).
2. `hooks.guard.main` parses the JSON. On a parse error it denies.
3. `policy.Policy.touches_sealed` checks file paths and scans shell commands for the store path, including globs, symlinks, and `cd`.
4. `hooks.guard.decide` returns allow or deny. A deny sends a reason to the agent.

## Record

1. Claude Code sends the finished tool call to `hooks.recorder`.
2. `hooks.recorder.record` matches a shell command against the configured patterns.
3. On a match, `ledger.Ledger.append` writes a record: kind, command, exit code, time, and the previous record hash.

## Evaluate

1. `cli.eval` calls `runner.run_holdout_eval` with the predictions file (untrusted).
2. The runner checks the budget in the ledger. If the budget is spent, it refuses and prints no score.
3. The runner runs the sealed evaluator in a subprocess against the sealed holdout and reads the metric.
4. The runner appends an `eval` record to the ledger.

## Audit

1. `cli.audit` loads the splits and, through the runner, the sealed holdout keys.
2. `audit.checks` produce findings: duplicates, entity overlap, target leakage, and preprocessing fit on full data.
3. The reviewer subagent reads the diff and the findings and adds comments. It does not edit files.

## Claim

1. `cli.claim` calls `ledger.Ledger.verify` and `seal.verify_manifest`. Any failure stops the claim.
2. `claim.stats` reads the trial count `N` from the ledger and adjusts the validation result.
3. `claim.report.build_report` writes the report: claim, sealed score, `N`, adjusted result, audit findings, and the stated limits of the guard. The report leaves the system as a Markdown file in the workspace.
