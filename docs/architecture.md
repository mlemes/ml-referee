# Architecture

This document describes the planned components of `ml-referee`. No code exists yet. Update this file in the same change as the code.

## Components

| Component | Path | Responsibility |
|---|---|---|
| Plugin manifest | `.claude-plugin/plugin.json` | Declares the plugin, its hooks, commands, skills, and agents. |
| Config | `src/ml_referee/config.py` | Loads and validates `.ml-referee.toml`. |
| Ledger | `src/ml_referee/ledger.py` | Append-only, hash-chained JSONL log. Counts trials. |
| Seal | `src/ml_referee/seal.py` | Splits data, writes the sealed store and the manifest. |
| Policy | `src/ml_referee/policy.py` | Decides whether a path or shell command touches sealed assets. |
| Guard hook | `src/ml_referee/hooks/guard.py` | `PreToolUse` entry point. Fails closed. |
| Recorder hook | `src/ml_referee/hooks/recorder.py` | `PostToolUse` entry point. Appends trials to the ledger. |
| Evaluator runner | `src/ml_referee/runner.py` | Runs the sealed evaluation within the holdout budget. |
| Audit | `src/ml_referee/audit/` | Deterministic leakage checks. |
| Claim | `src/ml_referee/claim/` | Statistics and report. |
| CLI | `src/ml_referee/cli.py` | `ml-referee seal|eval|audit|claim|verify-ledger`. |
| Slash commands, skills, agent | `commands/`, `skills/`, `agents/` | Claude Code surface for the CLI and the reviewer. |
| Attack bench | `bench/` | Scripted attacks and task definitions for the paper. |

## Boundaries

- **Workspace:** the project directory. The agent can read and write here.
- **Sealed store:** outside the workspace. Holds the holdout, the evaluator, the manifest, and the ledger. The agent must not touch it.
- **Trust boundary:** hooks and the CLI run as the same operating system user as the agent. This is a policy boundary, not a security boundary. See `docs/research/threat-model/threat-model.md`.

## Dependencies

- Hooks: Python standard library.
- Audit and claim: `pandas`, `numpy`, `scipy`.
- Development: `pytest`, `ruff`, `mypy`.

## Key decisions

See `docs/superpowers/specs/2026-11-02-ml-referee-design.md`.

## Deployment

Install as a Claude Code plugin from the repository. The plugin ships no service and no network component.
