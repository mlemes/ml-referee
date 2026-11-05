# Architecture

This document describes the components of `ml-referee` v0.1 as implemented. Update this file in the same change as the code.

## Components

| Component | Path | Responsibility |
|---|---|---|
| Plugin manifest | `.claude-plugin/plugin.json` | Declares the plugin name, version, and description. |
| Hook registration | `hooks/hooks.json` | Registers the guard on `PreToolUse` (`Read\|Write\|Edit\|Bash\|Grep\|Glob`) and the recorder on `PostToolUse` and `PostToolUseFailure` (`Bash`). Both pass `--harness claude` and run `python3 -m` with `PYTHONPATH` set to the plugin `src/`. |
| Config | `src/ml_referee/config.py` | Loads and validates `.ml-referee.toml`. Resolves the store path under `XDG_DATA_HOME`. Optional keys: `eval.predict_timeout_s` (default 600) and `data.group_hidden` (default false). Records the workspace directory (the config's parent). |
| Ledger | `src/ml_referee/ledger.py` | Append-only, hash-chained JSONL log under an `flock`. Kinds: `seal`, `trial`, `eval`. Stores the last hash in `head.txt`, written atomically. |
| Seal | `src/ml_referee/seal.py` | Moves the source data into the store, writes the holdout, evaluator, and manifest, and rewrites the workspace train file with the non-holdout rows. |
| Policy | `src/ml_referee/policy.py` | Decides whether a tool call touches the sealed store. Lets the agent run the plugin CLI (`audit`, `eval`, `verify-ledger`) inside ordinary compound commands. |
| Shell splitter | `src/ml_referee/shellparse.py` | Quote-aware lexer that splits a command line into simple commands (words and redirections). Returns nothing for syntax it does not model, and the guard then uses its strictest rules. |
| Harness adapter | `src/ml_referee/harness.py` | Normalizes each harness payload (Claude Code, Codex, Devin, agy, opencode) into canonical events. Maps the tool names and field keys of each harness. Formats each deny decision for the harness. Unknown tools normalize to `Other`. |
| Guard hook | `src/ml_referee/hooks/guard.py` | `PreToolUse` entry point. Takes `--harness NAME` (default: auto-detect). Normalizes the payload through the harness adapter and denies when any event is denied. Fails closed. |
| Recorder hook | `src/ml_referee/hooks/recorder.py` | `PostToolUse` entry point. Takes `--harness NAME`. Takes the exit code and failure flag from the canonical event. Appends matching trial commands to the ledger once the project is sealed. Never blocks. |
| Installer | `src/ml_referee/install.py` | `ml-referee install --harness NAME`. Writes or merges the hook file for each harness, tags every entry with `ml-referee`, and is idempotent. Supports `--dry-run`. Never overwrites a user's hook file. |
| AGENTS.md block | `src/ml_referee/agents_md.py` | Writes a block between `<!-- ml-referee:start -->` and `<!-- ml-referee:end -->` that tells the agent not to touch the sealed store or the config, lists the CLI commands it may run, and prints the holdout budget when the config is readable at install time. It is neutral: it never tells the agent to run a command. The installer writes it for Codex, Devin, agy, and opencode. |
| Packaged data | `src/ml_referee/data/opencode/ml-referee.js` | opencode plugin. Its `tool.execute.before` hook runs the guard with `--harness opencode` and throws on a deny. The installer renders it with the Python path and `PYTHONPATH` directory as constants (the plugin falls back to `python3`) and writes it to `.opencode/plugins/`. It ships as package data. |
| Evaluator runner | `src/ml_referee/runner.py` | Runs the sealed evaluator within the holdout budget, from a predictions file (`run_holdout_eval`) or from a predict command that receives the sealed rows without labels (`run_predict_eval`). |
| Audit | `src/ml_referee/audit/checks.py` | Deterministic leakage checks: exact duplicates, near duplicates (nearest-neighbor distance on standardized numeric features, `scipy.spatial.cKDTree`), entity overlap, target leak, and fit before split. |
| Claim | `src/ml_referee/claim/` | Statistics (`stats.py`) and report (`report.py`). |
| CLI | `src/ml_referee/cli.py` | `ml-referee seal` (optionally `--holdout-file` to seal an existing hidden split), `eval` (exactly one of `--predictions PATH` or `--predict-cmd CMD`), `audit` and `claim` (both accept repeatable `--against` files), `verify-ledger`, and `install`. |
| Commands, skill, agent | `commands/`, `skills/ml-referee/`, `agents/ml-reviewer.md` | Claude Code surface: `/ml-seal`, `/ml-audit`, `/ml-claim`, a workflow skill, and a read-only reviewer. |
| Attack bench | `bench/` | Eight scripted attacks (`bench/attacks/a1_*.py` to `a8_*.py`), a shared fixture (`bench/env.py`), and `bench/run_attacks.py`. |

## Boundaries

- **Workspace:** the project directory. The agent can read and write here.
- **Sealed store:** `${XDG_DATA_HOME:-~/.local/share}/ml-referee/<project-id>/`, mode `0700`. It holds `source.<ext>`, `holdout.parquet`, `evaluator.py`, `manifest.json`, `ledger.jsonl`, `head.txt`, and `eval.lock`. The agent must not touch it.
- **Trust boundary:** hooks and the CLI run as the same operating system user as the agent. This is a policy boundary, not a security boundary. See `docs/research/threat-model/threat-model.md`.

## Guard behavior

- **Fail closed.** `policy.decide` denies a malformed event, a non-string input, and any exception. `hooks.guard` denies when the event or the config cannot be validated. With no `.ml-referee.toml` found, it applies only the store-root rules below and allows the rest.
- **Config discovery.** The guard looks for `.ml-referee.toml` from the harness root first (`CLAUDE_PROJECT_DIR`, `DEVIN_PROJECT_DIR`, agy `workspacePaths`), then payload workspace or root fields, then the payload `cwd`, walking up from each. A `cwd` outside the workspace tree therefore no longer hides the config.
- **Store-root rules without a config.** `policy.decide_without_config` denies a resolved `cwd` inside `${XDG_DATA_HOME:-~/.local/share}/ml-referee/` (or the data directory that holds it, or a symlink into it), text that names the store root, and path arguments that resolve inside it. It allows the rest. The same rules also run when a config is found, so one config never unlocks the stores of other projects.
- **Marker checks.** `Policy.substring_touches` checks the store path, its parents, `.local/share`, `XDG_DATA_HOME`, and the store ancestors on the unstripped text, in both the raw and the punctuation-stripped form.
- **Bare `ml-referee` check.** Before this one check, the guard removes the workspace path and `.ml-referee.toml` in one pass, at path-component boundaries. Workspaces whose path contains `ml-referee` therefore work. The guard skips the removal when the store is inside the workspace.
- **Token checks.** `Policy.touches_sealed` also resolves each shell token with symlink-aware path resolution and expands globs. If shell parsing fails, it falls back to whitespace splitting.
- **Working directory.** The guard denies an event whose `cwd` resolves inside the store or into a symlink that points there. With a config, it also denies a `cwd` at a store ancestor that is not a workspace ancestor. A `cwd` at an ancestor that is also a workspace ancestor (such as `$HOME`) is not denied. Relative-path reads from there (`cat .local/share/ml-referee/x`, `cd ~ && cat .local/share/ml-referee/x`) are caught by the normalized-substring rules.
- **Write and Edit content.** The guard applies the substring rules only to `Write` content and `Edit` `new_string` (also a Codex patch body). It does no shell parsing there. It runs them in `prose` mode: the bare name `ml-referee` and the config name `.ml-referee.toml` in content are no access (a comment in `solution.py` or a `results.md` that mentions the tool). The name used as a path component (`ml-referee/`), the store path, its parents, `.local/share`, and `XDG_DATA_HOME` still deny. The `file_path` of a `Write` or `Edit` keeps the strict rules.
- **Here-document bodies.** `shellparse.split_heredocs` cuts a body out of a Bash command when it is inert: the delimiter is quoted (`<<'EOF'`, so nothing expands), the operator is outside quotes, the consumer is `cat` or `tee` with no pipe, substitution, or process substitution after the operator, and the terminator line exists. Every line outside the bodies must close its quotes, hold no backtick, `$(`, parenthesis, or brace (so no group or substitution can carry a body to a shell), and may not redefine commands (`alias`, `function`, `eval`, `PATH`, `command`, and similar). The body gets the `prose` rules above. The rest of the command (operator and terminator removed) goes through the normal rules, so the redirect target and any CLI line after the body are still judged. Anything else (unquoted delimiter, `<<-`, `<<<`, a pipe, `python <<'EOF'`, `bash <<'EOF'`, an unterminated body) keeps the old behaviour: the whole text is judged and any `ml-referee` mention denies.
- **Standalone glob tokens.** A glob whose every path component starts with `*`, `?`, a letter, a digit, `_`, or `-` cannot match a name that starts with a dot, so it cannot match `.ml-referee.toml` and no longer denies (`0.5 * a` in a heredoc, `grep -r x * | wc -l`). Components that start with `.`, `[`, `{`, `$`, `\`, `~`, or `?(` still expand with hidden files. The relaxation covers only a token the shell expands itself (no quote touches it): `find . -name '*.toml'` and `fnmatch` patterns stay denied. It is off for the whole command when, after quotes and backslashes are removed, it mentions `dotglob`, `extglob`, `glob_dots`, `shopt`, `setopt`, `GLOBIGNORE`, `BASHOPTS`, `BASH_ENV`, or `rcfile`. Plugin CLI arguments keep the strict check.
- **Config protection.** The guard denies `Write` and `Edit` of `.ml-referee.toml`. It denies a `Bash` command that names the file unless the command is a single read-only `cat`, `head`, `tail`, `less`, `ls`, `grep`, `wc`, or `stat`.
- **CLI segments.** `policy._cli_verdict` splits a Bash command with `shellparse.split_commands` (on `&& || ; | |& &`, parentheses, and newlines) and strips leading `VAR=value` assignments and the wrappers `env`, `time`, `nohup`, `timeout N`, and `uv run` (with only `--no-sync`, `--frozen`, `--locked`, `--offline`, `-q`). A segment is the plugin CLI when its first word is `ml-referee` or `python[3] -m ml_referee[.cli]`. Pipes, `2>&1`, `> file`, `timeout`, `cd dir &&`, subshells, `which ml-referee`, and `command -v ml-referee` therefore work. The command is judged as a whole and fails closed.
- **Agent CLI set.** The agent may run `audit` (flags `--against`, `--sources`), `eval` (flags `--predictions`, `--predict-cmd`), `verify-ledger`, and `<subcommand> --help`. `seal`, `claim`, `install`, `--config`, any other flag, and a missing subcommand are denied. `claim` spends the claim budget and belongs to the person.
- **CLI arguments and redirections.** Every argument and every redirection target (`>`, `>>`, `<`, `2>`, `&>`, `>&`) must stay outside the store, away from `.ml-referee.toml`, and free of `$`, backticks, and braces. The check resolves symlinks and expands globs against every directory the command can be in. A `--predict-cmd` value is checked as a command (its `{features}` and `{out}` placeholders are removed first). Assignments to `PATH`, `PYTHON*`, `LD_*`, `XDG_*`, `HOME`, `BASHOPTS`, and `CDPATH` are not accepted.
- **Other segments.** Every segment that is not the plugin CLI goes through the same store rules as before, with the segment text on its own, except that the read-only exemption for naming `.ml-referee.toml` (`cat .ml-referee.toml`) does not apply inside a line that runs the CLI. The command is denied outright when any segment assigns `PATH`, `PYTHON*`, `LD_*`, `XDG_*`, `HOME`, `GLOBIGNORE`, or `CDPATH`, names `shopt`, `alias`, `unalias`, `function`, `eval`, `source`, `builtin`, `enable`, `trap`, `coproc`, or `command` (other than `command -v ml-referee`) before the CLI, starts with `.`, or holds a `cd` that is not at the head of its segment (`command cd`). The config glob check includes hidden files and tests a token both whole and after its first `=`. `which ml-referee` and `command -v ml-referee` still have their other words and redirections checked. `cd dir` adds the directory to the set of directories that later segments are judged in. A dynamic `cd` (`cd $X`, `cd -`, `pushd`, `popd`) denies the command.
- **Not modelled.** A backslash-newline, `name()` or `()` function definitions, command substitution, backticks, process substitution, here documents that `split_heredocs` cannot prove inert, here strings, comments, `$'...'`, and an unterminated quote make the lexer return nothing. The guard then applies the old rules to the whole text, so any mention of `ml-referee` is denied. `eval "ml-referee ..."`, `bash -c`, `xargs ml-referee`, and `sudo ml-referee` also deny, because their segment is not the CLI.
- **CLI path.** The guard denies a CLI path such as `<workspace>/.venv/bin/ml-referee` by design. Use `ml-referee` on `PATH` or `python -m ml_referee`.
- **Unknown `~name`.** The shell leaves `~0.5` literal when no such user exists. `policy._expanduser` does the same. `Path.expanduser` raised for it, so prose such as `CV ~0.5` made the guard deny `Write`, `printf`, and `echo` text. `~`, `~/...`, `~+`, and `~-` still fail closed.
- **Known over-block.** The guard denies content that holds the name as a path (`ml-referee/...`), for example a comment that quotes `~/src/ml-referee/README`.
- **Accepted residuals.** A script the agent writes (through `Write` or an inert heredoc) is judged as content, so one that finds the store at run time (`Path.home().rglob('ml-referee')`) or runs `cat > x.sh <<'EOF'; sh x.sh` passes; this follows from treating the name in content as no access. A shell option set outside the command line (a startup file) can make `*` match hidden files. Shell variable splitting and base64 or hex decoding that rebuild the store path at run time, `find ~ -name 'holdout.parquet' -exec cat`, glob-obfuscated `cd` chains, and inline Python that walks `~` pass the guard.
- **Fail-open on missing interpreter.** If `python3` is missing, the hook process crashes. Claude Code treats that crash as non-blocking, so the guard fails open.

## Recorder behavior

The recorder records only when the project is sealed, that is, when `manifest.json` exists in the store. It never creates the store.

The recorder splits a command into segments on `;`, `&&`, `||`, `|`, `&`, and newlines. It first removes redirections (`> file`, `2>&1`). For each segment it strips leading environment assignments and the wrappers `nohup`, `env`, `time`, `nice`, `setsid`, `exec`, `command`, `sh -c`, `bash -c`, `uv run` (with flags), `poetry run`, and `pipenv run`, and it reduces an interpreter path or version (`.venv/bin/python`, `python3.12`) to `python`. It then matches each configured `trial_patterns` entry against the raw command and against every normalized segment. A command outside the patterns is invisible to the ledger. Attack A7 reports 0 percent blocked for this reason.

## Runner behavior

The sections below describe the runner. `run_predict_eval` shares the lock, budget check, evaluator run, and ledger write with `run_holdout_eval` (see `_budget_lock` and `_score`).

`run_holdout_eval` verifies the store, the ledger, and the manifest before it does anything else. It takes an `flock` on `eval.lock`, checks the budget, and runs the evaluator in its own session with a minimal environment and a 60 second timeout. On timeout it kills the evaluator process group. Errors never include evaluator output. It appends an `eval` record only after the row count matches the manifest.

**Claim reserve.** `eval.claim_reserve` (default 1 when `holdout.budget` is at least 2, else 0; it must be below the budget) keeps the last evaluations for the final claim. Both runner entry points take a `role` (`agent` by default, `claim` from `claim`). Under the lock, an `agent` eval is refused with "Remaining budget is reserved for the final claim" when `budget - spent <= claim_reserve`. A `claim` eval can spend the reserve. The role is stored in the `eval` ledger record. The agent cannot pass `claim`: the CLI `eval` always uses `agent`, and the guard denies the `claim` subcommand. Setting `claim_reserve = 0` restores the old behavior.

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
- `check_trial_count` adds a medium `many_trials` finding when the ledger holds more than `audit.trial_threshold` (default 20) `trial` records: the public score is selection-biased if the final model was chosen among them on that score. `claim` applies a Bonferroni adjustment over the same count when you pass `--p-value` and adds a "Selection warning" section above the threshold. Both flag and adjust. Neither can prove that selection happened, because the count shows only how many chances there were.
- For non-binary numeric labels, the target-leak check uses Spearman rank correlation. A value of at least 0.98 is a high finding. A value of at least 0.83 whose one-sided 95% Fisher lower bound is at least 0.78 is a medium "noisy copy" finding, which keeps detection when the feature is the label plus noise up to about half the label standard deviation (synthetic tests: 30 of 30 seeds at noise 0.5, n=300). An honest feature with R-squared 0.6 (rho about 0.76) is not flagged. The rule measures predictive strength only. It cannot separate a leak from a very strong honest feature (R-squared of 0.7 or more can be flagged). Binary labels still use AUC at 0.98.

## Key decisions

See `docs/superpowers/specs/2026-11-02-ml-referee-design.md`.

## Deployment

Install as a Claude Code plugin from the repository with `claude --plugin-dir .`. Install the package so that the `ml-referee` command is on `PATH`. The plugin ships no service and no network component.

### Predict command

`ml-referee eval --predict-cmd "CMD"` lets an agent that cannot see the sealed rows get a score. `CMD` must contain `{features}` and `{out}`. Under the `eval.lock` the runner verifies the store and refuses when the budget is spent, before it creates any temp file. It then writes the sealed rows without the label column (and without the group column when `data.group_hidden = true`) to a private temp directory (mode `0700`), runs `CMD` through `shlex` with no shell, in the workspace, with the timeout from `eval.predict_timeout_s` (default 600 s), and kills the process group when it ends. The command's output goes to an unlinked temp file (no path the predictor can replace) and only its last 1000 bytes appear in an error. The runner refuses a symlink or non-regular file at `{out}`, reads it, requires one column, the sealed row count, and no missing or infinite values, and writes a validated copy for the evaluator. It scores that copy with the sealed evaluator, appends an `eval` record that adds `predict_cmd_sha256` (the hash of the command, not the features), and removes the temp directory in a `finally` block. A failure before scoring (the command fails, times out, writes a bad file, or the evaluator fails) spends no budget, because no score was produced. A successful run prints only the metric, the score, and `n`.

Residual: the predictor runs as the same OS user as the agent and sees the unlabeled sealed features (never the labels). The group column stays in the features by default so a model can use entity features; set `data.group_hidden = true` to remove it. The guard checks the command text, not what the predictor program does at run time.
