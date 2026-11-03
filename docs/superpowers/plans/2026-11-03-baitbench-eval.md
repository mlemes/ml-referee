# BAITBENCH evaluation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the BAITBENCH tasks (Prasad et al., arXiv:2608.30724) in Docker containers with and without `ml-referee`, and report whether the plugin detects the planted shortcuts and corrects the reported score.

**Architecture:** Two small product features let `ml-referee` seal a hidden split that already exists and audit the agent-visible test file. A Docker image holds the agent CLIs and the BAITBENCH task generator. A host driver builds each task, runs a scripted or real agent in a container under condition C0 (no plugin) or C3 (plugin), and scores the final `solution.py` on the public and the hidden split.

**Tech Stack:** Python 3.11 (plugin), BAITBENCH generator (Python 3.14 via `uv`), Docker 29, the agent CLIs `claude`, `codex`, and `agy`.

**Spec:** `docs/research/evaluation/protocol.md` (RQ1 to RQ4) and the BAITBENCH repository (MIT): https://github.com/juanjvazquez/BAITBENCH

## Global constraints

- Hook modules and `config.py` stay standard library only.
- Existing tests stay green. The gate before every commit: `.venv/bin/ruff check . && .venv/bin/mypy src && .venv/bin/python -m pytest -q`.
- Third-party code (BAITBENCH) lives outside the repo under `/tmp/baitbench-*` and runs only inside containers.
- Agent credentials are copied, never mounted writable, and the copy is deleted after the run.
- Each run writes its result to disk as soon as it finishes. A rerun skips finished runs.
- Docs follow the Google developer documentation style guide. Hooks are a policy layer, not a sandbox.

## Review focus

1. `seal --holdout-file` with a file whose columns differ from the train file. Expected: refuse with a clear error, move nothing.
2. `audit --against` where the visible file has the label column. Expected: the label is dropped before duplicate checks, and the output contains counts only.
3. A hidden file with a row count of zero, or unreadable. Expected: error, store not created.
4. `seal --holdout-file` when the hidden file and the train file share rows. Expected: sealed anyway, and the audit later reports the overlap.
5. The sealed holdout file stays byte-identical to the input (no reordering).

---

### Task 1: Seal an existing hidden split

**Files:**
- Modify: `src/ml_referee/seal.py`, `src/ml_referee/cli.py`
- Test: `tests/test_seal.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `seal(cfg: Config, evaluator_src: Path, holdout_src: Path | None = None) -> Manifest`. With `holdout_src`, the file is validated (same columns as the source data, at least one row), copied into the store as `holdout.parquet`, the workspace train file is left unchanged, and `manifest.n_holdout` is its row count. CLI: `ml-referee seal --evaluator PATH [--holdout-file PATH]`.

- [ ] **Step 1: Write the failing tests** for the five review-focus cases above, plus: the sealed holdout equals the input rows in order; the workspace train file keeps all rows; the source data is still moved into the store; the ledger gets one `seal` record.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest -q tests/test_seal.py tests/test_cli.py`. Expected: FAIL.
- [ ] **Step 3: Implement** `holdout_src` in `seal.seal` and the `--holdout-file` option in `cli.py`.
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: seal an existing hidden split`.

### Task 2: Audit the agent-visible test file

**Files:**
- Modify: `src/ml_referee/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `ml-referee audit [--against PATH ...]` and `ml-referee claim` accept `--against PATH` (repeatable). For each file the CLI runs `check_duplicates`, `check_entity_overlap`, and `check_target_leak`-free comparisons of the workspace train file against that file (label dropped, `group` column from the config), and tags each finding with `against=<basename>`. Output stays counts only.

- [ ] **Step 1: Write the failing tests:** a visible test file that shares entity ids with train gives a high `entity_overlap` finding tagged with the file name; a visible file with exact duplicate rows gives a high `duplicates` finding; a clean visible file gives none; the output never contains a label value; `claim --against` embeds the findings in the report.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest -q tests/test_cli.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the option in `cli.py`.
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: audit the agent-visible test file`.

### Task 3: Container harness and scripted agents

**Files:**
- Create: `bench/baitbench/Dockerfile`, `bench/baitbench/driver.py`, `bench/baitbench/scripted.py`, `bench/baitbench/score.py`, `tests/test_baitbench_driver.py`

**Interfaces:**
- Produces:
  - `build_tasks(out_dir: Path, seeds: range) -> list[TaskRef]` runs the BAITBENCH generator in a container and returns the task directories (`regression_task_01`, `regression_task_02`, `classification_task_03`, size `n10k`, prompt condition `score`).
  - `run_one(task: TaskRef, condition: str, agent: str, budget_min: int, out_dir: Path) -> RunResult` where `condition` is `C0` or `C3` and `agent` is `scripted-honest`, `scripted-hack`, `claude`, `codex`, or `agy`. It writes `result.json` (public score, hidden score, ml-referee sealed score and audit findings for C3, trial count, wall time, quota notes) and skips finished runs.
  - `score_solution(task: TaskRef, solution_dir: Path) -> dict` scores a final `solution.py` on the public and the hidden `robust_test.csv`, inside a container.
- Container rules: non-root user, `--cpus 2 --memory 4g --pids-limit 512`, no host mounts except the task workspace and a copied credential directory, hidden `robust_test.csv` never placed in the agent workspace.
- C3 setup: copy the workspace, write `.ml-referee.toml`, run `ml-referee seal --evaluator <hidden scorer> --holdout-file robust_test.csv`, `ml-referee install --harness <agent>`, add the AGENTS.md block, and finish with `ml-referee claim --against test.csv`.

- [ ] **Step 1: Write the failing tests** for the pure parts: result schema, skip-finished behavior, task list, and the scripted solutions (honest ridge, entity-mean hack for task 01, nearest-neighbor copy hack for task 02, public-label overfit for task 03). Docker-dependent tests are marked `integration` and skipped without Docker.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest -q tests/test_baitbench_driver.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the harness. Build the image once and reuse it.
- [ ] **Step 4: Run the gate and one container smoke run** for `scripted-hack` on task 01 in C0. Expected: the public score is far better than the hidden score.
- [ ] **Step 5: Commit** `feat: add BAITBENCH container harness`.

### Task 4: Scripted agent matrix

- [ ] **Step 1: Run** the matrix `{3 tasks} x {seeds 1..3} x {scripted-honest, scripted-hack} x {C0, C3}` through `driver.py`. Expected: 36 `result.json` files.
- [ ] **Step 2: Summarize** into `docs/research/evaluation/baitbench-scripted.md`: per task and condition, the public score, the hidden score, the sealed score, the audit findings, and whether the claim report exposes the gap.
- [ ] **Step 3: Commit** `docs: record scripted BAITBENCH results`.

### Task 5: Real agent calibration runs

- [ ] **Step 1: Run one task (task 01) for each of `claude`, `codex`, and `agy` in C0 and C3**, 15 minutes each, one container at a time. Record wall time and the quota percentage before and after each run.
- [ ] **Step 2: Save** transcripts and `result.json` under `bench/results/baitbench/<date>/` (ignored by git) and a summary in `docs/research/evaluation/baitbench-agents.md`.
- [ ] **Step 3: Report** the quota cost per run so the full run size can be chosen.
- [ ] **Step 4: Commit** `docs: record BAITBENCH calibration runs`.
