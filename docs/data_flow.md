# Data flow

This document traces data through `ml-referee` v0.1 as implemented. Update it in the same change as the code.

## Seal

1. You run `ml-referee seal --evaluator <path>` in your terminal, outside the agent's tools. Claude's `/ml-referee:ml-seal` explains this command but does not execute it; the guard denies agent-driven sealing. `cli.main` finds `.ml-referee.toml` and calls `config.load_config` (TOML, trusted: written by you).
2. `seal.seal` refuses if the store exists. It reads the training file (CSV or Parquet, trusted).
3. `seal.seal` picks holdout units with a seeded permutation. A unit is a `group` value when set, otherwise a row.
4. `seal.seal` creates the store with mode `0700` and moves the source file into it as `source.<ext>` (mode `0400`).
5. `seal.seal` writes `holdout.parquet`, `evaluator.py`, and `manifest.json` (SHA-256 hashes, seed, metric, direction, and holdout row count).
6. `ledger.Ledger.append` writes the `seal` record, which holds the manifest.
7. `seal.seal` rewrites the workspace train file with the non-holdout rows. There is no validation split. On any failure it restores the source file and removes the store.
8. You see only the holdout row count.

With `--holdout-file PATH`, `seal.seal` validates the file first (same columns as the source data, at least one row, label present) and raises `SealError` before it creates the store. It then copies the file into the store as `holdout.parquet` in row order, copies the source data into the store as `source.<ext>`, and leaves the workspace train file unchanged. It skips the seeded split.

## Guard

1. The harness sends a tool call to `hooks.guard` as JSON on standard input (untrusted: the agent chose it). `--harness NAME` selects the adapter, or the guard auto-detects it.
2. `harness.normalize` maps the payload into canonical events. It maps each harness's tool names and field keys, such as `CommandLine` to `command` for agy, and `apply_patch` to one write event per file header. Unknown tools become `Other`. A malformed payload, a missing or non-string `cwd`, or a missing field raises an error, which produces a deny.
3. `hooks.guard._evaluate` collects config roots in order of trust: the harness root (`CLAUDE_PROJECT_DIR` for Claude Code, `DEVIN_PROJECT_DIR` for Devin, `workspacePaths` for agy), any workspace or root field of the payload (`workspace_roots`, `workspaceRoots`, `workspace_root`, `project_dir`, and similar), the process directory for agy and Devin, and last the payload `cwd`. It walks up from each root and uses the first `.ml-referee.toml` it finds, loads it, and builds a `Policy`. A malformed event or an invalid config produces a deny.
3a. With no config anywhere, `policy.decide_without_config` applies the store-root rules, which need no config. It denies an event whose resolved `cwd` is inside the store root `${XDG_DATA_HOME:-~/.local/share}/ml-referee/` (symlinks resolved first) or is the directory that holds it. It denies literal text that names the store root (absolute path, `.local/share/ml-referee`, `$XDG_DATA_HOME/ml-referee`, in raw and punctuation-stripped form) and any shell token or path argument that resolves inside the store root, including glob expansions. It collapses repeated slashes and `/./` before matching and splits tokens on shell punctuation and quotes, so `bash -c "cat ~/.local/share//ml-referee/x"` is caught. When a config is found, the guard runs these store-root rules in addition to the config policy, so a decoy config cannot unlock the stores of other projects. From an ancestor `cwd` such as `$HOME`, it also denies the relative spelling of the root (for example `cat .local/share/ml-referee/x`). Everything else is allowed, so a repository without the plugin config keeps working. If `normalize` cannot parse the payload and no config exists, the guard scans every payload string with the same rules. The trust boundary is the payload: the harness root and the environment are trusted, the tool arguments are not. Residual cases: shell variable splitting and base64 or hex decoding that hide the path, and a bare `ls` from an ancestor above the data directory.
4. `policy.decide` checks the string inputs of each canonical event. It denies `Write` and `Edit` of `.ml-referee.toml` and `Bash` commands that modify it. `Other` events get every string checked as a candidate path (so `.ml-referee.toml` and the store are denied) and as content, and their `cwd` is validated even when they carry no strings. For agy, the config search starts at `workspacePaths[0]` (`config_root`), never at the tool's `Cwd` argument.
5. `Policy.substring_touches` runs the marker checks (store path, parents, `.local/share`, `XDG_DATA_HOME`, ancestors) on the unstripped text. It removes the workspace path and `.ml-referee.toml` only before the bare `ml-referee` check, and skips that removal when the store is inside the workspace. `Write` content and `Edit` `new_string` get only these substring rules, in `prose` mode: the bare `ml-referee` check becomes a check for the name as a path component (`ml-referee/`), so a comment or report that names the tool is not an access.
5a. For a Bash command, `shellparse.split_heredocs` first cuts out the bodies of inert `cat`/`tee` here-documents (quoted delimiter, no pipe or substitution); each body goes through the `prose` substring rules and the remaining text continues below. Then `policy._cli_verdict` runs. `shellparse.split_commands` splits the text into simple commands and redirections (nothing is returned for substitution, here documents, comments, `$'..'`, or an unterminated quote). `_unwrap` strips assignments and `env`, `time`, `nohup`, `timeout N`, `uv run`. A segment whose first word is `ml-referee` or `python -m ml_referee[.cli]` is the plugin CLI. Its subcommand must be `audit`, `eval`, or `verify-ledger` (or `--help`), each flag must be on that subcommand's list, and each argument must pass `_value_denied` (no `$`, backtick, or brace; not the config; `touches_sealed` false in every directory a `cd` can reach). Every redirection target of every segment passes the same check. All other segments go through the rules below one at a time (without the read-only exemption for the config). A line with a shell-state builtin, an unsafe assignment, or a misplaced `cd` is denied. A command with no CLI segment, or one the lexer cannot read, goes through the rules below as a whole.
6. `Policy.touches_sealed` resolves shell tokens with symlink-aware paths and expands globs. The config-glob check (`policy._config_glob`) skips a glob that cannot match a dotfile (every component starts with `*`, `?`, or a plain character) unless the command mentions `dotglob`, `shopt`, or a related option. A shell parse error falls back to whitespace splitting. `decide` also denies a `cwd` inside the store or at a store ancestor.
7. `harness.format_decision` renders the result in the deny contract of the harness. Claude Code gets `permissionDecision` JSON on standard output. Codex gets exit code 2 with stderr. agy gets `{"decision":"deny","reason":"..."}`. A PostToolUse event for agy gets `{}`. Claude Code output is byte-identical to the previous release.

## Ledger write

`Ledger.append` writes `head.txt` atomically (a `0600` temporary file, then `os.replace`). It repairs a head that a crash left one record behind. Any other mismatch is an integrity failure.

## Record

1. The harness sends the finished Bash call to `hooks.recorder --harness NAME` (`PostToolUse` or `PostToolUseFailure`). The recorder normalizes the payload and takes `exit_code` and `failed` from it. For Claude Code, it reads `tool_response.exit_code`. For opencode, the plugin forwards `exit_code`.
2. The recorder proceeds only if `manifest.json` exists in the store. It never creates the store. `recorder._segments` normalizes the command: it removes redirections (`> file`, `>> file`, `2>&1`, `&> file`, `< file`), splits on `&&`, `||`, `;`, `|`, `&`, and newlines, strips leading parentheses, then strips leading `VAR=value` assignments and the wrappers `env`, `time`, `nohup`, `nice`, `setsid`, `exec`, `command`, `sh -c`, `bash -c`, `uv run` (with its flags and flag values), `poetry run`, and `pipenv run`. A `cd x &&` prefix is its own segment, so it never hides the command. An interpreter path or version (`/tmp/venv/bin/python3`, `python3.12`) is reduced to `python`, and the flags `-u`, `-B`, `-O`, `-OO`, `-I`, `-s`, `-S`, and `-E` are dropped.
3. `recorder.record` matches each `trial_patterns` entry against the whitespace-normalized raw command and against every normalized segment. A pattern matches a candidate on a glob match, on equality, or as a prefix followed by a space. Only the first word of a segment is an interpreter, so `echo python evaluate.py`, `cat evaluate.py`, and `pip install evaluate` do not match `python evaluate.py*`. A command outside the patterns is not recorded.
4. On a match, `ledger.Ledger.append` writes a `trial` record: command, exit code, failure flag, and the SHA-256 of the train file. For Codex, a post-tool payload without authoritative status records both `exit_code` and `failed` as null. Structured integer exit codes and explicit failure events retain known outcomes. The recorder never infers status from stdout; unknown trials still count. Historical records are left untouched.
5. The recorder never raises. It logs errors to standard error and exits 0.

## Evaluate

1. `cli.eval` calls `runner.run_holdout_eval` with the predictions file (untrusted), or `runner.run_predict_eval` when `--predict-cmd` is given (see "Evaluate with a predict command").
2. The runner verifies the store: `Ledger.verify`, `seal.verify_manifest`, and a check that the first ledger record matches the manifest.
3. The runner takes an `flock` on `eval.lock`, verifies again, and refuses if the ledger holds `holdout_budget` `eval` records, or, for the `agent` role, if the remaining budget is at most `claim_reserve`. A refusal prints no score. The appended `eval` record carries the `role` (`agent` or `claim`).
4. The runner resolves the predictions path. It must be a file outside the store.
5. The runner starts `evaluator.py` in a new session with the holdout and predictions paths, a minimal environment, and a 60 second timeout. On timeout it kills the process group.
6. The runner parses one JSON object with `value` and `n` from standard output. It checks `n` against the manifest.
7. The runner appends an `eval` record. Errors never include evaluator output.

## Evaluate with a predict command

1. `cli.eval --predict-cmd CMD` passes `CMD` (untrusted text; the guard already checked it) to `runner.run_predict_eval`. A command without `{features}` and `{out}` or with bad quoting raises `ValueError` before anything else happens.
2. `runner._budget_lock` verifies the store, takes the `flock`, and refuses when the ledger holds `holdout_budget` `eval` records or the reserve rule applies (see above). No temp file exists yet.
3. The runner reads `holdout.parquet` (trusted), drops the label column (and the group column when `data.group_hidden` is true), and writes `features.csv` into a `0700` temp directory (trust boundary: the predictor sees this file, never the labels).
4. `runner._run_predictor` runs `CMD` with `{features}` and `{out}` replaced, without a shell, in the workspace (`cfg.workspace`), with the `eval.predict_timeout_s` timeout (default 600 s). Its output goes to an unlinked temp file. The runner kills the process group on exit or timeout.
5. `runner._read_predictions` reads `{out}` (untrusted CSV; a symlink or non-regular file is refused): one column, `n_holdout` rows, no missing values, finite numbers. It writes a validated copy `scored.csv`.
6. `runner._score` runs the sealed evaluator on the copy exactly as for `--predictions`, checks `n`, and appends an `eval` record that also holds `predict_cmd_sha256`.
7. A `finally` block removes the temp directory. A failure at steps 3 to 5, or an evaluator failure, appends no record and spends no budget.
8. `cli` prints the metric, the score, and `n`.

## Audit

1. `cli.audit` verifies the store, then loads the workspace train file and the sealed holdout.
2. `audit.checks` produce findings: duplicates (the label column is ignored), near duplicates (numeric features standardized with the training statistics, nearest training row per holdout row through a `cKDTree`, label ignored, counts only), entity overlap, target leakage (Spearman rank correlation for non-binary labels: high at 0.98, medium noisy-copy finding at 0.83 with a lower-bound guard), and fit before split in the files you pass with `--sources`.
3. `cli.audit` also reads the `trial` count from the ledger and adds a medium `many_trials` finding when it exceeds `audit.trial_threshold` (default 20). `cli.audit` prints one JSON line per finding. It exits 3 when any finding is high severity.
4. The `ml-reviewer` agent reads the diff and the findings and reports without editing files.
5. For each `--against PATH` file, `cli.audit` also compares the workspace train file with that file (label dropped, group column from the config), runs the duplicate, near-duplicate, and entity-overlap checks, and tags each finding with `[against=<basename>]`. A missing or unreadable file exits 1 before `claim` spends any holdout budget.

Audit and claim counts reveal holdout membership to an agent that can run the CLI. This is an accepted residual.

## Claim

1. You run `ml-referee claim --predictions <path>` in your terminal, outside the agent's tools. Claude's `/ml-referee:ml-claim` explains this command but does not execute it; the guard denies agent-driven claims. Optional inputs are `--val-scores` (a JSON list), `--p-value`, and `--out`.
2. `cli.claim` validates `--out` (not a directory, writable, outside the store) before it spends the budget, then runs the audit with no source files to collect findings.
3. `claim.report.claim` verifies the store, then reads the trial count `N` from the ledger.
4. `claim.report.claim` calls `runner.run_holdout_eval` with role `claim`, which may spend the reserved budget. This consumes one holdout evaluation, even if you ran `eval` before.
5. `claim.report.build_report` applies `bonferroni(p_value, N)` when you supply a p-value and `selection_gap` when you supply validation scores.
6. When `N` exceeds `audit.trial_threshold`, `build_report` adds a "Selection warning" section (with the Bonferroni-adjusted p-value when one was supplied). It flags and adjusts. It cannot prove selection.
7. The report lists the sealed score, `N`, the adjusted result, the audit findings, and the statement that hooks are a policy layer, not a sandbox. It goes to `--out` or standard output.

## Scripted attack bench

1. `bench.run_attacks.main` calls `bench.env.make_env`, which seals a synthetic fixture in a temporary `XDG_DATA_HOME`.
2. `run_all` runs each attack under C0, C2, and C3. C3 calls `policy.decide` on synthetic events. C2 matches literal protected paths. C0 blocks nothing.
3. The bench prints a Markdown table of block rates.
