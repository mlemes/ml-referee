# Architecture

This document describes the components of `ml-referee` v0.1 as implemented. Update this file in the same change as the code.

## Components

| Component | Path | Responsibility |
|---|---|---|
| Plugin manifest | `.claude-plugin/plugin.json` | Declares the plugin name, version, and description. |
| Hook registration | `hooks/hooks.json` | Registers the guard on `PreToolUse` (`Read\|Write\|Edit\|Bash\|Grep\|Glob`) and the recorder on `PostToolUse` and `PostToolUseFailure` (`Bash`). Both run `python3 -m` with `PYTHONPATH` set to the plugin `src/`. |
| Config | `src/ml_referee/config.py` | Loads and validates `.ml-referee.toml`. Resolves the store path under `XDG_DATA_HOME`. |
| Ledger | `src/ml_referee/ledger.py` | Append-only, hash-chained JSONL log under an `flock`. Kinds: `seal`, `trial`, `eval`. Stores the last hash in `head.txt`, written atomically. |
| Seal | `src/ml_referee/seal.py` | Moves the source data into the store, writes the holdout, evaluator, and manifest, and rewrites the workspace train file with the non-holdout rows. |
| Policy | `src/ml_referee/policy.py` | Decides whether a tool call touches the sealed store. |
| Guard hook | `src/ml_referee/hooks/guard.py` | `PreToolUse` entry point. Fails closed. |
| Recorder hook | `src/ml_referee/hooks/recorder.py` | `PostToolUse` entry point. Appends matching trial commands to the ledger once the project is sealed. Never blocks. |
| Evaluator runner | `src/ml_referee/runner.py` | Runs the sealed evaluator within the holdout budget. |
| Audit | `src/ml_referee/audit/checks.py` | Deterministic leakage checks. |
| Claim | `src/ml_referee/claim/` | Statistics (`stats.py`) and report (`report.py`). |
| CLI | `src/ml_referee/cli.py` | `ml-referee seal`, `eval`, `audit`, `claim`, and `verify-ledger`. |
| Commands, skill, agent | `commands/`, `skills/ml-referee/`, `agents/ml-reviewer.md` | Claude Code surface: `/ml-seal`, `/ml-audit`, `/ml-claim`, a workflow skill, and a read-only reviewer. |
| Attack bench | `bench/` | Eight scripted attacks (`bench/attacks/a1_*.py` to `a8_*.py`), a shared fixture (`bench/env.py`), and `bench/run_attacks.py`. |

## Boundaries

- **Workspace:** the project directory. The agent can read and write here.
- **Sealed store:** `${XDG_DATA_HOME:-~/.local/share}/ml-referee/<project-id>/`, mode `0700`. It holds `source.<ext>`, `holdout.parquet`, `evaluator.py`, `manifest.json`, `ledger.jsonl`, `head.txt`, and `eval.lock`. The agent must not touch it.
- **Trust boundary:** hooks and the CLI run as the same operating system user as the agent. This is a policy boundary, not a security boundary. See `docs/research/threat-model/threat-model.md`.

## Guard behavior

- **Fail closed.** `policy.decide` denies a malformed event, a non-string input, and any exception. `hooks.guard` denies when the event or the config cannot be validated. It allows the call when no `.ml-referee.toml` exists above the working directory.
- **Marker checks.** `Policy.substring_touches` checks the store path, its parents, `.local/share`, `XDG_DATA_HOME`, and the store ancestors on the unstripped text, in both the raw and the punctuation-stripped form.
- **Bare `ml-referee` check.** Before this one check, the guard removes the workspace path and `.ml-referee.toml` in one pass, at path-component boundaries. Workspaces whose path contains `ml-referee` therefore work. The guard skips the removal when the store is inside the workspace.
- **Token checks.** `Policy.touches_sealed` also resolves each shell token with symlink-aware path resolution and expands globs. If shell parsing fails, it falls back to whitespace splitting.
- **Working directory.** The guard denies an event whose `cwd` is inside the store or at a store ancestor.
- **Write and Edit content.** The guard applies the substring rules only to `Write` content and `Edit` `new_string`. It does no shell parsing there.
- **Config protection.** The guard denies `Write` and `Edit` of `.ml-referee.toml`. It denies a `Bash` command that names the file unless the command is a single read-only `cat`, `head`, `tail`, `less`, `ls`, `grep`, `wc`, or `stat`.
- **CLI exception.** A single `ml-referee ...` or `python -m ml_referee ...` command is checked without its program name. The agent can therefore run the CLI itself and get at most `holdout_budget` sealed looks.
- **CLI path.** The guard denies a CLI path such as `<workspace>/.venv/bin/ml-referee` by design. Use `ml-referee` on `PATH` or `python -m ml_referee`.
- **Known over-block.** The guard denies a quoted concatenation of the config name in `Write` content.
- **Accepted residuals.** `find ~ -name 'holdout.parquet' -exec cat`, glob-obfuscated `cd` chains, and inline Python that walks `~` pass the guard.
- **Fail-open on missing interpreter.** If `python3` is missing, the hook process crashes. Claude Code treats that crash as non-blocking, so the guard fails open.

## Recorder behavior

The recorder records only when the project is sealed, that is, when `manifest.json` exists in the store. It never creates the store.

The recorder splits a command into segments on `;`, `&&`, `||`, `|`, `&`, and newlines. For each segment it strips leading environment assignments and the wrappers `nohup`, `env`, `time`, `nice`, `setsid`, `exec`, `command`, and `sh -c` or `bash -c`. It then matches each configured `trial_patterns` entry per segment. A command outside the patterns is invisible to the ledger. Attack A7 reports 0 percent blocked for this reason.

## Runner behavior

`run_holdout_eval` verifies the store, the ledger, and the manifest before it does anything else. It takes an `flock` on `eval.lock`, checks the budget, and runs the evaluator in its own session with a minimal environment and a 60 second timeout. On timeout it kills the evaluator process group. Errors never include evaluator output. It appends an `eval` record only after the row count matches the manifest.

## Ledger integrity

Each record holds the hash of its predecessor. `head.txt` holds the last hash, so deleting the tail of the ledger is detected. `Ledger` writes it atomically: a `0600` temporary file, then `os.replace`. If a crash leaves `head.txt` one record behind, the next `append` repairs it. Every other mismatch is an integrity failure. `head.txt` lives in the same store. A same-user agent that can write the store can forge both files. This is a known limit. Version 2 needs a separate user or a container.

## Dependencies

- Hooks and ledger: Python standard library. The seal, audit, and claim modules need `pandas`, `numpy`, `scipy`, and `pyarrow`.
- Development: `pytest`, `ruff`, `mypy`, `pandas-stubs`.

## Known gaps

- `Ledger._verify` does not reject unknown record kinds in an existing file. Only `append` checks the kind.
- `load_manifest` does not validate field types.

## CLI behavior

- `audit` duplicate detection ignores the label column.
- `audit` and `claim` counts reveal holdout membership to an agent that can run the CLI. This is an accepted residual.
- `claim` validates `--out` before it spends the budget. The path must not be a directory, must be writable, and must be outside the store.
- For non-binary labels, the target-leak check uses Spearman rank correlation.

## Key decisions

See `docs/superpowers/specs/2026-11-02-ml-referee-design.md`.

## Deployment

Install as a Claude Code plugin from the repository with `claude --plugin-dir .`. Install the package so that the `ml-referee` command is on `PATH`. The plugin ships no service and no network component.
