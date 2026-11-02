# ml-referee implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Claude Code plugin that seals the holdout and evaluator, guards them with hooks, logs trials in a hash-chained ledger, audits leakage, and issues a trial-adjusted claim.

**Architecture:** A Python package `ml_referee` holds all logic. Two stdlib-only hook scripts call into it. A CLI exposes `seal`, `eval`, `audit`, `claim`, and `verify-ledger`. Plugin commands, skills, and one reviewer agent wrap the CLI. A `bench/` folder holds the attacks that support the paper.

**Tech Stack:** Python 3.11, `pytest`, `ruff`, `mypy`, `pandas`, `numpy`, `scipy`, TOML via `tomllib`.

**Spec:** `docs/superpowers/specs/2026-11-02-ml-referee-design.md`

## Global constraints

- Python `>=3.11`.
- Hook modules import the standard library only.
- Median hook latency target: below 100 ms.
- No network access at run time.
- Package `ml_referee`, command `ml-referee`, plugin name `ml-referee`.
- Sealed store: `${XDG_DATA_HOME:-~/.local/share}/ml-referee/<project-id>/`.
- Config file: `.ml-referee.toml`. Default holdout budget: 1.
- Ledger: JSONL, each record holds the SHA-256 of the previous record.
- Docs follow the Google developer documentation style guide. Update `docs/architecture.md` and `docs/data_flow.md` in the same change as the code.
- The gate before every commit: `ruff check . && mypy src && pytest -q`.

## Review focus

Each line names an input the spec implies but no task would otherwise test. The owning task holds the test.

1. A shell command reaches the store through a glob, a symlink, `cd`, or a variable (`cat $HOME/.local/share/ml-referee/*/holdout*`). Expected: deny. Owner: Task 5.
2. The ledger is truncated, edited, or reordered. Expected: `verify` fails and `claim` refuses. Owner: Task 3.
3. The holdout budget is spent. Expected: `eval` refuses and prints no score. Owner: Task 7.
4. The guard gets malformed JSON or an unknown tool name. Expected: deny with a reason for path-touching tools, never a silent allow. Owner: Task 5.
5. The entity column has missing values or is absent. Expected: a finding, not a crash. Owner: Task 8.

---

### Task 1: Scaffold and gate

**Files:**
- Create: `pyproject.toml`, `src/ml_referee/__init__.py`, `.claude-plugin/plugin.json`, `tests/test_smoke.py`

**Interfaces:**
- Produces: package `ml_referee` with `__version__: str`; a valid plugin manifest with `name == "ml-referee"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_smoke.py`

```python
def test_package_has_version():
    import ml_referee

    assert ml_referee.__version__ == "0.1.0"


def test_manifest_name():
    import json, pathlib

    m = json.loads(pathlib.Path(".claude-plugin/plugin.json").read_text())
    assert m["name"] == "ml-referee"
```

- [ ] **Step 2: Run** `pytest -q tests/test_smoke.py`. Expected: FAIL (module and file missing).
- [ ] **Step 3: Create the files.** `pyproject.toml` sets Python `>=3.11`, the `ml-referee` console script `ml_referee.cli:main`, and `ruff` and `mypy` strict settings.
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "chore: scaffold package and plugin manifest"`.

### Task 2: Config

**Files:**
- Create: `src/ml_referee/config.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Config: project_id: str; train_path: Path; label: str; group: str | None; eval_command: str; metric: str; higher_is_better: bool; trial_patterns: tuple[str, ...]; holdout_budget: int; holdout_fraction: float; seed: int`
  - `load_config(path: Path) -> Config`
  - `store_dir(cfg: Config) -> Path`
  - `class ConfigError(ValueError)`

- [ ] **Step 1: Write the failing tests**

```python
def test_load_valid_config(tmp_path): ...  # all fields parsed, budget defaults to 1
def test_missing_label_raises(tmp_path): ...  # pytest.raises(ConfigError)
def test_budget_below_one_raises(tmp_path): ...  # holdout_budget = 0 -> ConfigError
def test_store_dir_uses_xdg(
    tmp_path, monkeypatch
): ...  # XDG_DATA_HOME honored, ends with project_id
```

- [ ] **Step 2: Run** `pytest -q tests/test_config.py`. Expected: FAIL.
- [ ] **Step 3: Implement** with `tomllib`. Validate required keys and ranges (`0 < holdout_fraction < 1`).
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: load and validate config`.

### Task 3: Ledger

**Files:**
- Create: `src/ml_referee/ledger.py`, `tests/test_ledger.py`

**Interfaces:**
- Consumes: `Config` from Task 2 (only `store_dir`).
- Produces:
  - `@dataclass(frozen=True) class Record: index: int; kind: str; payload: dict[str, object]; time: str; prev_hash: str; hash: str`
  - `class Ledger: __init__(self, path: Path)`; `append(self, kind: str, payload: dict[str, object]) -> Record`; `records(self) -> list[Record]`; `verify(self) -> VerifyResult`; `trial_count(self) -> int`; `eval_count(self) -> int`
  - `@dataclass(frozen=True) class VerifyResult: ok: bool; reason: str`
  - Kinds: `"trial"`, `"eval"`, `"seal"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_append_chains_hashes(tmp_path):      # r2.prev_hash == r1.hash; r1.prev_hash == "0"*64
def test_verify_ok_on_clean_log(tmp_path): ...
def test_verify_fails_on_edited_payload(tmp_path): ...    # edit a line -> ok False, "hash mismatch" in reason
def test_verify_fails_on_deleted_middle_record(tmp_path): ...
def test_verify_fails_on_reordered_records(tmp_path): ...
def test_trial_count_counts_only_trial_kind(tmp_path): ...  # 2 trial + 1 eval + 1 seal -> 2
def test_concurrent_append_keeps_chain(tmp_path): ...       # two processes, chain still verifies (use fcntl lock)
```

- [ ] **Step 2: Run** `pytest -q tests/test_ledger.py`. Expected: FAIL.
- [ ] **Step 3: Implement** `Ledger` with an `fcntl` file lock. Hash the canonical JSON of the record without `hash`. Truncation of the tail is detectable only against the head hash stored at seal time, so `append` also writes the head hash to `head.txt` next to the ledger, and `verify` compares it.
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add hash-chained ledger`.

### Task 4: Seal

**Files:**
- Create: `src/ml_referee/seal.py`, `tests/test_seal.py`

**Interfaces:**
- Consumes: `Config`, `store_dir`, `Ledger`.
- Produces:
  - `@dataclass(frozen=True) class Manifest: holdout_sha256: str; evaluator_sha256: str; seed: int; metric: str; higher_is_better: bool; n_holdout: int`
  - `seal(cfg: Config, evaluator_src: Path) -> Manifest`. It moves the source data into the store and rewrites the workspace train file with the non-holdout rows. It creates no validation split.
  - `verify_manifest(cfg: Config) -> bool`
  - `load_manifest(cfg: Config) -> Manifest`

- [ ] **Step 1: Write the failing tests**

```python
def test_seal_removes_holdout_from_workspace(...):  # train file no longer has holdout rows; store has holdout
def test_seal_is_reproducible_for_same_seed(...):   # same split twice
def test_seal_groups_never_straddle(...):           # no group id in both train and holdout
def test_seal_refuses_second_run(...):              # raises SealError unless store absent
def test_verify_manifest_detects_evaluator_edit(...):  # edit sealed evaluator -> False
def test_seal_appends_seal_record(...):             # ledger has one "seal" record
```

- [ ] **Step 2: Run** `pytest -q tests/test_seal.py`. Expected: FAIL.
- [ ] **Step 3: Implement** `seal`. Use a seeded group-aware split with `numpy`. Set store files to mode `0400` and the directory to `0700`.
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: seal holdout and evaluator`.

### Task 5: Policy and guard hook

**Files:**
- Create: `src/ml_referee/policy.py`, `src/ml_referee/hooks/__init__.py`, `src/ml_referee/hooks/guard.py`, `tests/test_policy.py`, `tests/test_guard.py`

**Interfaces:**
- Consumes: `Config`, `store_dir`.
- Produces:
  - `@dataclass(frozen=True) class Policy: store: Path; workspace: Path`
  - `build_policy(cfg: Config, workspace: Path) -> Policy`
  - `Policy.touches_sealed(self, path_or_command: str) -> bool`
  - `@dataclass(frozen=True) class Decision: allow: bool; reason: str`
  - `decide(event: dict[str, object], policy: Policy) -> Decision`
  - `main() -> int` reads one JSON event from standard input and prints the Claude Code `PreToolUse` decision JSON.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize(
    "cmd",
    [
        "cat {store}/holdout.parquet",
        "cat $HOME/.local/share/ml-referee/*/holdout*",
        "cd {store} && ls",
        "ln -s {store} link && cat link/holdout.parquet",
        "python -c \"open('{store}/holdout.parquet')\"",
        "X={store}; cat $X/holdout.parquet",
    ],
)
def test_bash_touching_store_denied(
    cmd, policy
): ...  # decide(...).allow is False, reason names the store


def test_edit_of_sealed_evaluator_denied(policy): ...
def test_read_of_store_path_denied(policy): ...
def test_train_command_on_workspace_allowed(policy): ...  # "python train.py" -> allow
def test_malformed_json_denies_path_tools(
    capsys,
): ...  # main() on b"{not json" -> deny output
def test_unknown_tool_name_without_paths_allowed(policy): ...
def test_guard_median_latency_under_100ms(policy): ...  # 200 calls, median < 0.1 s
```

- [ ] **Step 2: Run** `pytest -q tests/test_policy.py tests/test_guard.py`. Expected: FAIL.
- [ ] **Step 3: Implement** `touches_sealed`. Resolve `~`, `$HOME`, and `$VAR` assignments seen earlier in the same command. Expand globs against the real store listing. Follow symlinks created in the command. Treat any unresolved expansion near the store prefix as a match (fail closed).
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add policy and guard hook`.

### Task 6: Recorder hook

**Files:**
- Create: `src/ml_referee/hooks/recorder.py`, `tests/test_recorder.py`

**Interfaces:**
- Consumes: `Config`, `Ledger`.
- Produces: `record(event: dict[str, object], cfg: Config, ledger: Ledger) -> Record | None`; `main() -> int`.

- [ ] **Step 1: Write the failing tests**

```python
def test_matching_command_appends_trial(...):     # "python train.py --lr 3e-4" with pattern "python train.py" -> kind "trial"
def test_nonmatching_command_ignored(...):        # "ls" -> None, ledger empty
def test_failed_command_still_recorded(...):      # exit_code 1 recorded, payload["exit_code"] == 1
def test_non_bash_tool_ignored(...): ...
```

- [ ] **Step 2: Run** `pytest -q tests/test_recorder.py`. Expected: FAIL.
- [ ] **Step 3: Implement** with `fnmatch` against `cfg.trial_patterns`. Store the command, exit code, and a SHA-256 of the files named by the config.
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add recorder hook`.

### Task 7: Sealed evaluation runner

**Files:**
- Create: `src/ml_referee/runner.py`, `tests/test_runner.py`

**Interfaces:**
- Consumes: `Config`, `Ledger`, `Manifest`, `verify_manifest`.
- Produces:
  - `@dataclass(frozen=True) class EvalResult: metric: str; value: float; n: int`
  - `run_holdout_eval(cfg: Config, predictions: Path) -> EvalResult`
  - `class BudgetExhausted(RuntimeError)`, `class IntegrityError(RuntimeError)`
  - The evaluator contract: it runs as `python <evaluator> <holdout> <predictions>` and prints one JSON object `{"value": float, "n": int}` on standard output.

- [ ] **Step 1: Write the failing tests**

```python
def test_first_eval_returns_value_and_logs(...): ...
def test_second_eval_raises_budget_exhausted_and_prints_no_score(...): ...  # BudgetExhausted; ledger gets no new eval record
def test_tampered_evaluator_raises_integrity_error(...): ...
def test_evaluator_nonzero_exit_does_not_consume_budget(...): ...
```

- [ ] **Step 2: Run** `pytest -q tests/test_runner.py`. Expected: FAIL.
- [ ] **Step 3: Implement** with `subprocess.run`, a timeout, and an empty environment apart from `PATH`.
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add sealed evaluation runner`.

### Task 8: Leakage audit

**Files:**
- Create: `src/ml_referee/audit/__init__.py`, `src/ml_referee/audit/checks.py`, `tests/test_audit.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Finding: check: str; severity: str; detail: str` (severity is `"high"`, `"medium"`, or `"info"`)
  - `check_duplicates(train: pd.DataFrame, holdout_keys: pd.DataFrame) -> list[Finding]`
  - `check_entity_overlap(train: pd.DataFrame, holdout: pd.DataFrame, group: str | None) -> list[Finding]`
  - `check_target_leak(train: pd.DataFrame, label: str, auc_threshold: float = 0.98) -> list[Finding]`
  - `check_fit_on_full(sources: list[Path]) -> list[Finding]` (static `ast` heuristic: `fit` or `fit_transform` called on a name that also feeds the split)

- [ ] **Step 1: Write the failing tests**

```python
def test_duplicate_rows_across_splits_flagged_high(): ...
def test_clean_split_has_no_findings(): ...
def test_entity_overlap_flagged(): ...
def test_group_column_absent_returns_info_not_crash(): ...  # review focus 5
def test_group_column_with_nan_returns_finding_not_crash(): ...  # review focus 5
def test_single_feature_with_auc_1_flagged_as_target_leak(): ...
def test_scaler_fit_before_split_flagged(tmp_path): ...
def test_scaler_fit_after_split_not_flagged(tmp_path): ...
```

- [ ] **Step 2: Run** `pytest -q tests/test_audit.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** Hash rows for duplicates. Use a rank-based AUC from `scipy.stats` for single features.
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add leakage audit checks`.

### Task 9: Claim statistics and report

**Files:**
- Create: `src/ml_referee/claim/__init__.py`, `src/ml_referee/claim/stats.py`, `src/ml_referee/claim/report.py`, `tests/test_claim.py`

**Interfaces:**
- Consumes: `Ledger`, `EvalResult`, `Finding`, `Manifest`.
- Produces:
  - `bonferroni(p: float, n: int) -> float`
  - `bh_adjust(ps: list[float]) -> list[float]`
  - `selection_gap(validation_scores: list[float], holdout: float, higher_is_better: bool = True) -> float` (max validation minus holdout, or holdout minus min validation when lower is better)
  - `build_report(manifest: Manifest, result: EvalResult, trials: int, findings: list[Finding], validation_scores: list[float], p_value: float | None = None, higher_is_better: bool | None = None) -> str`
  - `claim(cfg: Config, predictions: Path, validation_scores: list[float] | None = None, p_value: float | None = None, findings: list[Finding] | None = None, higher_is_better: bool | None = None) -> str` (verifies ledger and manifest, runs one sealed eval, returns the report)

- [ ] **Step 1: Write the failing tests**

```python
def test_bonferroni_caps_at_one():
    assert bonferroni(0.04, 50) == 1.0


def test_bonferroni_scales():
    assert bonferroni(0.01, 5) == pytest.approx(0.05)


def test_bh_matches_known_example(): ...  # [0.01, 0.04, 0.03, 0.005] -> [0.02, 0.04, 0.04, 0.02]
def test_selection_gap_positive_when_val_exceeds_holdout(): ...
def test_report_contains_trial_count_and_limits_sentence(): ...
def test_claim_refuses_when_ledger_invalid(): ...  # raises IntegrityError
```

- [ ] **Step 2: Run** `pytest -q tests/test_claim.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** The report always includes: claim, sealed score, trial count, adjusted result, audit findings, and one sentence stating that hooks are a policy layer, not a sandbox.
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add claim statistics and report`.

### Task 10: CLI and plugin wiring

**Files:**
- Create: `src/ml_referee/cli.py`, `hooks/hooks.json`, `commands/ml-seal.md`, `commands/ml-audit.md`, `commands/ml-claim.md`, `skills/ml-referee/SKILL.md`, `agents/ml-reviewer.md`, `tests/test_cli.py`
- Modify: `.claude-plugin/plugin.json`

**Interfaces:**
- Consumes: all earlier modules.
- Produces: `main(argv: list[str] | None = None) -> int` with subcommands `seal`, `eval`, `audit`, `claim`, `verify-ledger`. `hooks/hooks.json` registers `guard` on `PreToolUse` for `Read|Write|Edit|Bash|Grep|Glob` and `recorder` on `PostToolUse` for `Bash`, both through `${CLAUDE_PLUGIN_ROOT}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_cli_verify_ledger_exit_codes(...):      # 0 on clean, 1 on tampered
def test_cli_eval_second_call_exits_nonzero(...):
def test_hooks_json_registers_guard_and_recorder(): ...
def test_end_to_end_seal_eval_claim(tmp_path): ...   # synthetic data; claim report contains trial count
```

- [ ] **Step 2: Run** `pytest -q tests/test_cli.py`. Expected: FAIL.
- [ ] **Step 3: Implement** with `argparse`. The reviewer agent file instructs a model other than the author to read the diff and the audit findings and to report without editing.
- [ ] **Step 4: Run the gate and install the plugin locally.** Run `claude --plugin-dir .` in a scratch project and confirm `/ml-seal` appears. Expected: command listed.
- [ ] **Step 5: Commit** `feat: add CLI and plugin wiring`.

### Task 11: Attack bench and pilot

**Files:**
- Create: `bench/attacks/a1_evaluator_edit.py` through `bench/attacks/a8_metric_swap.py`, `bench/run_attacks.py`, `bench/tasks/README.md`, `tests/test_bench.py`

**Interfaces:**
- Consumes: `decide`, `Policy`, `Ledger`, `run_holdout_eval`.
- Produces: `run_all(env: AttackEnv, conditions: tuple[str, ...] = ("C0", "C2", "C3")) -> dict[str, dict[str, float]]`. It returns the block rate per attack and condition. Build `env` with `bench.env.make_env(tmp_path)`. A bare `Policy` raises `TypeError`. Conditions: `"C0"`, `"C2"`, `"C3"` from `docs/research/evaluation/protocol.md`. C1 is an agent condition and is excluded here.

- [ ] **Step 1: Write the failing tests**

```python
def test_each_attack_class_has_a_script(): ...  # A1..A8 present
def test_c3_blocks_all_scripted_attacks_a1_to_a2(): ...  # block rate == 1.0 for A1, A2 incl. 3 obfuscations
def test_c0_blocks_nothing(): ...  # block rate == 0.0
```

- [ ] **Step 2: Run** `pytest -q tests/test_bench.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the attack scripts as lists of synthetic tool-call events. Do not run an agent here.
- [ ] **Step 4: Run a pilot with two agent models** on one BAITBENCH task, 3 runs per condition. Record variance and compute the run count for the full study. Save raw output under `bench/results/<date>/` (ignored by git).
- [ ] **Step 5: Commit** `feat: add scripted attack bench`.

### Task 12: Docs and paper sync

**Files:**
- Modify: `docs/architecture.md`, `docs/data_flow.md`, `docs/research/paper/ml-referee.md`, `docs/research/evaluation/protocol.md`, `README.md`

- [ ] **Step 1:** Compare both core docs with the code. Fix every difference.
- [ ] **Step 2:** Fill the paper's evaluation section only with results from the protocol. Mark any missing result as pending.
- [ ] **Step 3:** Re-run the tooling search in `docs/research/related-work/landscape.md` and add new rows with the date.
- [ ] **Step 4:** Verify every reference in `references.bib` still resolves.
- [ ] **Step 5: Commit** `docs: sync architecture, data flow, and paper`.
