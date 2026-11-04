# Architecture

This document describes the components of `ml-referee` v0.1 as implemented. Update this file in the same change as the code.

## Components

| Component | Path | Responsibility |
|---|---|---|
| Plugin manifest | `.claude-plugin/plugin.json` | Declares the plugin name, version, and description. |
| Hook registration | `hooks/hooks.json` | Registers the guard on `PreToolUse` (`Read\|Write\|Edit\|Bash\|Grep\|Glob`) and the recorder on `PostToolUse` and `PostToolUseFailure` (`Bash`). Both pass `--harness claude` and run `python3 -m` with `PYTHONPATH` set to the plugin `src/`. |
| Config | `src/ml_referee/config.py` | Loads and validates `.ml-referee.toml`. Resolves the store path under `XDG_DATA_HOME`. |
| Ledger | `src/ml_referee/ledger.py` | Append-only, hash-chained JSONL log under an `flock`. Kinds: `seal`, `trial`, `eval`. Stores the last hash in `head.txt`, written atomically. |
| Seal | `src/ml_referee/seal.py` | Moves the source data into the store, writes the holdout, evaluator, and manifest, and rewrites the workspace train file with the non-holdout rows. |
| Policy | `src/ml_referee/policy.py` | Decides whether a tool call touches the sealed store. |
| Harness adapter | `src/ml_referee/harness.py` | Normalizes each harness payload (Claude Code, Codex, Devin, agy, opencode) into canonical events. Maps the tool names and field keys of each harness. Formats each deny decision for the harness. Unknown tools normalize to `Other`. |
| Guard hook | `src/ml_referee/hooks/guard.py` | `PreToolUse` entry point. Takes `--harness NAME` (default: auto-detect). Normalizes the payload through the harness adapter and denies when any event is denied. Fails closed. |
| Recorder hook | `src/ml_referee/hooks/recorder.py` | `PostToolUse` entry point. Takes `--harness NAME`. Takes the exit code and failure flag from the canonical event. Appends matching trial commands to the ledger once the project is sealed. Never blocks. |
| Installer | `src/ml_referee/install.py` | `ml-referee install --harness NAME`. Writes or merges the hook file for each harness, tags every entry with `ml-referee`, and is idempotent. Supports `--dry-run`. Never overwrites a user's hook file. |
| AGENTS.md block | `src/ml_referee/agents_md.py` | Writes a block between `<!-- ml-referee:start -->` and `<!-- ml-referee:end -->` that tells the agent not to touch the sealed store or the config. The installer writes it for Codex, Devin, agy, and opencode. |
| Packaged data | `src/ml_referee/data/opencode/ml-referee.js` | opencode plugin. Its `tool.execute.before` hook runs the guard with `--harness opencode` and throws on a deny. The installer renders it with the Python path and `PYTHONPATH` directory as constants (the plugin falls back to `python3`) and writes it to `.opencode/plugins/`. It ships as package data. |
| Evaluator runner | `src/ml_referee/runner.py` | Runs the sealed evaluator within the holdout budget. |
| Audit | `src/ml_referee/audit/checks.py` | Deterministic leakage checks: exact duplicates, near duplicates (nearest-neighbor distance on standardized numeric features, `scipy.spatial.cKDTree`), entity overlap, target leak, and fit before split. |
| Claim | `src/ml_referee/claim/` | Statistics (`stats.py`) and report (`report.py`). |
| CLI | `src/ml_referee/cli.py` | `ml-referee seal` (optionally `--holdout-file` to seal an existing hidden split), `eval`, `audit` and `claim` (both accept repeatable `--against` files), `verify-ledger`, and `install`. |
| Commands, skill, agent | `commands/`, `skills/ml-referee/`, `agents/ml-reviewer.md` | Claude Code surface: `/ml-seal`, `/ml-audit`, `/ml-claim`, a workflow skill, and a read-only reviewer. |
| Attack bench | `bench/` | Eight scripted attacks (`bench/attacks/a1_*.py` to `a8_*.py`), a shared fixture (`bench/env.py`), and `bench/run_attacks.py`. |

## Boundaries

- **Workspace:** the project directory. The agent can read and write here.
- **Sealed store:** `${XDG_DATA_HOME:-~/.local/share}/ml-referee/<project-id>/`, mode `0700`. It holds `source.<ext>`, `holdout.parquet`, `evaluator.py`, `manifest.json`, `ledger.jsonl`, `head.txt`, and `eval.lock`. The agent must not touch it.
- **Trust boundary:** hooks and the CLI run as the same operating system user as the agent. This is a policy boundary, not a security boundary. See `docs/research/threat-model/threat-model.md`.

## Guard behavior

- **Fail closed.** `policy.decide` denies a malformed event, a non-string input, and any exception. `hooks.guard` denies when the event or the config cannot be validated. With no `.ml-referee.toml` found, it applies only the store-root rules below and allows the rest.
- **Config discovery.** The guard looks for `.ml-referee.toml` from the harness root first (`CLAUDE_PROJECT_DIR`, `DEVIN_PROJECT_DIR`, agy `workspacePaths`), then payload workspace or root fields, then the payload `cwd`, walking up from each. A `cwd` outside the workspace tree therefore no longer hides the config.
- **Store-root rules without a config.** `policy.decide_without_config` denies a resolved `cwd` inside `${XDG_DATA_HOME:-~/.local/share}/ml-referee/` (or the data directory that holds it, or a symlink into it), text that names the store root, and path arguments that resolve inside it. It allows the rest.
- **Marker checks.** `Policy.substring_touches` checks the store path, its parents, `.local/share`, `XDG_DATA_HOME`, and the store ancestors on the unstripped text, in both the raw and the punctuation-stripped form.
- **Bare `ml-referee` check.** Before this one check, the guard removes the workspace path and `.ml-referee.toml` in one pass, at path-component boundaries. Workspaces whose path contains `ml-referee` therefore work. The guard skips the removal when the store is inside the workspace.
- **Token checks.** `Policy.touches_sealed` also resolves each shell token with symlink-aware path resolution and expands globs. If shell parsing fails, it falls back to whitespace splitting.
- **Working directory.** The guard denies an event whose `cwd` resolves inside the store or into a symlink that points there. With a config, it also denies a `cwd` at a store ancestor that is not a workspace ancestor. A `cwd` at an ancestor that is also a workspace ancestor (such as `$HOME`) is not denied. Relative-path reads from there (`cat .local/share/ml-referee/x`, `cd ~ && cat .local/share/ml-referee/x`) are caught by the normalized-substring rules.
- **Write and Edit content.** The guard applies the substring rules only to `Write` content and `Edit` `new_string`. It does no shell parsing there.
- **Config protection.** The guard denies `Write` and `Edit` of `.ml-referee.toml`. It denies a `Bash` command that names the file unless the command is a single read-only `cat`, `head`, `tail`, `less`, `ls`, `grep`, `wc`, or `stat`.
- **CLI exception.** A single `ml-referee ...` or `python -m ml_referee ...` command is checked without its program name. The agent can therefore run the CLI itself and get at most `holdout_budget` sealed looks.
- **CLI path.** The guard denies a CLI path such as `<workspace>/.venv/bin/ml-referee` by design. Use `ml-referee` on `PATH` or `python -m ml_referee`.
- **Known over-block.** The guard denies a quoted concatenation of the config name in `Write` content.
- **Accepted residuals.** Shell variable splitting and base64 or hex decoding that rebuild the store path at run time, `find ~ -name 'holdout.parquet' -exec cat`, glob-obfuscated `cd` chains, and inline Python that walks `~` pass the guard.
- **Fail-open on missing interpreter.** If `python3` is missing, the hook process crashes. Claude Code treats that crash as non-blocking, so the guard fails open.

## Recorder behavior

The recorder records only when the project is sealed, that is, when `manifest.json` exists in the store. It never creates the store.

The recorder splits a command into segments on `;`, `&&`, `||`, `|`, `&`, and newlines. It first removes redirections (`> file`, `2>&1`). For each segment it strips leading environment assignments and the wrappers `nohup`, `env`, `time`, `nice`, `setsid`, `exec`, `command`, `sh -c`, `bash -c`, `uv run` (with flags), `poetry run`, and `pipenv run`, and it reduces an interpreter path or version (`.venv/bin/python`, `python3.12`) to `python`. It then matches each configured `trial_patterns` entry against the raw command and against every normalized segment. A command outside the patterns is invisible to the ledger. Attack A7 reports 0 percent blocked for this reason.

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

- `audit` duplicate and near-duplicate detection ignore the label column.
- `check_near_duplicates` standardizes the shared numeric columns with the training mean and standard deviation, fills missing values with the training median, and flags holdout rows whose nearest training row is at most the 0.2th percentile of the training-to-training nearest-neighbor distances (`NEAR_DUP_QUANTILE`). It reports medium above 1 percent of the holdout rows and high above 5 percent (at least 3 rows), and it prints counts only. It caps each side at 20,000 rows with a fixed-seed subsample. The quantile came from development seeds that the experiments repository does not use. A near duplicate whose distance exceeds that threshold, such as a copy with large noise in a dense region, stays undetected.
- `audit` and `claim` counts reveal holdout membership to an agent that can run the CLI. This is an accepted residual.
- `claim` validates `--out` before it spends the budget. The path must not be a directory, must be writable, and must be outside the store.
- For non-binary labels, the target-leak check uses Spearman rank correlation.

## Key decisions

See `docs/superpowers/specs/2026-11-02-ml-referee-design.md`.

## Deployment

Install as a Claude Code plugin from the repository with `claude --plugin-dir .`. Install the package so that the `ml-referee` command is on `PATH`. The plugin ships no service and no network component.
