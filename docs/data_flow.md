# Data flow

This document traces data through `ml-referee` v0.1 as implemented. Update it in the same change as the code.

## Seal

1. You run `/ml-seal`, which calls `ml-referee seal --evaluator <path>`. `cli.main` finds `.ml-referee.toml` and calls `config.load_config` (TOML, trusted: written by you).
2. `seal.seal` refuses if the store exists. It reads the training file (CSV or Parquet, trusted).
3. `seal.seal` picks holdout units with a seeded permutation. A unit is a `group` value when set, otherwise a row.
4. `seal.seal` creates the store with mode `0700` and moves the source file into it as `source.<ext>` (mode `0400`).
5. `seal.seal` writes `holdout.parquet`, `evaluator.py`, and `manifest.json` (SHA-256 hashes, seed, metric, direction, and holdout row count).
6. `ledger.Ledger.append` writes the `seal` record, which holds the manifest.
7. `seal.seal` rewrites the workspace train file with the non-holdout rows. There is no validation split. On any failure it restores the source file and removes the store.
8. You see only the holdout row count.

## Guard

1. The harness sends a tool call to `hooks.guard` as JSON on standard input (untrusted: the agent chose it). `--harness NAME` selects the adapter, or the guard auto-detects it.
2. `harness.normalize` maps the payload into canonical events. It maps each harness's tool names and field keys, such as `CommandLine` to `command` for agy, and `apply_patch` to one write event per file header. Unknown tools become `Other`. A malformed payload, a missing or non-string `cwd`, or a missing field raises an error, which produces a deny.
3. `hooks.guard.main` walks up from `cwd` to `.ml-referee.toml`, loads it, and builds a `Policy`. A malformed event or an invalid config produces a deny. No config means allow.
4. `policy.decide` checks the string inputs of each canonical event. It denies `Write` and `Edit` of `.ml-referee.toml` and `Bash` commands that modify it. `Other` events get every string checked as a candidate path (so `.ml-referee.toml` and the store are denied) and as content, and their `cwd` is validated even when they carry no strings. For agy, the config search starts at `workspacePaths[0]` (`config_root`), never at the tool's `Cwd` argument.
5. `Policy.substring_touches` runs the marker checks (store path, parents, `.local/share`, `XDG_DATA_HOME`, ancestors) on the unstripped text. It removes the workspace path and `.ml-referee.toml` only before the bare `ml-referee` check, and skips that removal when the store is inside the workspace. `Write` content and `Edit` `new_string` get only these substring rules.
6. `Policy.touches_sealed` resolves shell tokens with symlink-aware paths and expands globs. A shell parse error falls back to whitespace splitting. `decide` also denies a `cwd` inside the store or at a store ancestor.
7. `harness.format_decision` renders the result in the deny contract of the harness. Claude Code gets `permissionDecision` JSON on standard output. Codex gets exit code 2 with stderr. agy gets `{"decision":"deny","reason":"..."}`. A PostToolUse event for agy gets `{}`. Claude Code output is byte-identical to the previous release.

## Ledger write

`Ledger.append` writes `head.txt` atomically (a `0600` temporary file, then `os.replace`). It repairs a head that a crash left one record behind. Any other mismatch is an integrity failure.

## Record

1. The harness sends the finished Bash call to `hooks.recorder --harness NAME` (`PostToolUse` or `PostToolUseFailure`). The recorder normalizes the payload and takes `exit_code` and `failed` from it. For Claude Code, it reads `tool_response.exit_code`. For opencode, the plugin forwards `exit_code`.
2. The recorder proceeds only if `manifest.json` exists in the store. It never creates the store. `recorder._segments` splits the command into segments and strips environment assignments and wrappers.
3. `recorder.record` matches `trial_patterns` per segment. A command outside the patterns is not recorded.
4. On a match, `ledger.Ledger.append` writes a `trial` record: command, exit code, failure flag, and the SHA-256 of the train file.
5. The recorder never raises. It logs errors to standard error and exits 0.

## Evaluate

1. `cli.eval` calls `runner.run_holdout_eval` with the predictions file (untrusted).
2. The runner verifies the store: `Ledger.verify`, `seal.verify_manifest`, and a check that the first ledger record matches the manifest.
3. The runner takes an `flock` on `eval.lock`, verifies again, and refuses if the ledger holds `holdout_budget` `eval` records. A refusal prints no score.
4. The runner resolves the predictions path. It must be a file outside the store.
5. The runner starts `evaluator.py` in a new session with the holdout and predictions paths, a minimal environment, and a 60 second timeout. On timeout it kills the process group.
6. The runner parses one JSON object with `value` and `n` from standard output. It checks `n` against the manifest.
7. The runner appends an `eval` record. Errors never include evaluator output.

## Audit

1. `cli.audit` verifies the store, then loads the workspace train file and the sealed holdout.
2. `audit.checks` produce findings: duplicates (the label column is ignored), entity overlap, target leakage (Spearman rank correlation for non-binary labels), and fit before split in the files you pass with `--sources`.
3. `cli.audit` prints one JSON line per finding. It exits 3 when any finding is high severity.
4. The `ml-reviewer` agent reads the diff and the findings and reports without editing files.

Audit and claim counts reveal holdout membership to an agent that can run the CLI. This is an accepted residual.

## Claim

1. You run `/ml-claim`, which calls `ml-referee claim --predictions <path>`. Optional inputs are `--val-scores` (a JSON list), `--p-value`, and `--out`.
2. `cli.claim` validates `--out` (not a directory, writable, outside the store) before it spends the budget, then runs the audit with no source files to collect findings.
3. `claim.report.claim` verifies the store, then reads the trial count `N` from the ledger.
4. `claim.report.claim` calls `runner.run_holdout_eval`. This consumes one holdout evaluation, even if you ran `eval` before.
5. `claim.report.build_report` applies `bonferroni(p_value, N)` when you supply a p-value and `selection_gap` when you supply validation scores.
6. The report lists the sealed score, `N`, the adjusted result, the audit findings, and the statement that hooks are a policy layer, not a sandbox. It goes to `--out` or standard output.

## Scripted attack bench

1. `bench.run_attacks.main` calls `bench.env.make_env`, which seals a synthetic fixture in a temporary `XDG_DATA_HOME`.
2. `run_all` runs each attack under C0, C2, and C3. C3 calls `policy.decide` on synthetic events. C2 matches literal protected paths. C0 blocks nothing.
3. The bench prints a Markdown table of block rates.

## BAITBENCH scripted runs

1. `driver.build_tasks` runs the BAITBENCH generator in a container and writes tasks under `/tmp/baitbench-work/make_datasets/tasks/generated/`.
2. `driver.run_one` copies the task files except `robust_test.csv` into a temporary workspace (`prepare_workspace`), then starts one container per stage, all sharing `/work`, `/state` (the sealed store through `XDG_DATA_HOME`), and `/out`.
3. C3 only, stage `setup` (`inside.setup`): writes `.ml-referee.toml`, runs `ml-referee seal --holdout-file` on a staged copy of the hidden file, deletes the staged copy, and writes the `AGENTS.md` block. The hidden file is mounted read-only in this stage and in `finalize`, never in `agent`.
4. Stage `agent` (`inside.agent`): writes each scripted `solution.py` version, runs `python evaluate.py`, and in C3 pipes a synthetic Claude `PostToolUse` event into `ml_referee.hooks.recorder` after each run.
5. Stage `finalize` (`inside.finalize`): `score.score_workspace` scores the final solution on `test.csv` and on the hidden file (public and hidden scores, hidden predictions to a temporary file). In C3 it also runs `ml-referee audit --against test.csv` and `ml-referee claim` with the hidden predictions.
6. The driver reads `inside.json`, adds `gap_present` and `claim_exposes_gap` against the honest hidden baseline, and writes `result.json` atomically. A rerun skips runs whose `result.json` has no error.


## BAITBENCH real-agent runs

1. `driver._execute_real` stages one credential file into a temporary `0700` home (`real.staged_home`), copies the task without `robust_test.csv`, and writes `timer.py`.
2. For C3, the setup stage seals the hidden split and installs the hooks (trust boundary: the hidden file is mounted only here and in finalize).
3. The agent stage runs `timeout <budget> /opt/agents/<agent> ...` with network enabled; stdout and stderr return to the host and are redacted of credential fragments before they are saved under `bench/results/baitbench/<date>/real/` (gitignored).
4. The finalize stage scores `solution.py` on the public and hidden splits and, for C3, runs audit and claim with the agent's recorded scores (`real.val_scores`).
5. `result.json` adds `exit_status`, `hook_denials`, and `hidden_probes`.
