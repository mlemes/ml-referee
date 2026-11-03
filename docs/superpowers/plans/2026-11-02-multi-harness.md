# ml-referee multi-harness implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the `ml-referee` guard and recorder under Claude Code, Codex, Devin for Terminal, Antigravity (`agy`), and opencode, and give each harness a one-command installer.

**Architecture:** One normalization layer turns each harness payload into the canonical event the guard already understands, and one formatter turns the guard decision into each harness's deny contract. The hook entry points keep one code path and take `--harness NAME`. An installer writes each harness's config. opencode gets a small JavaScript plugin that calls the same Python entry points.

**Tech Stack:** Python 3.11 standard library for the hooks and installer, one JavaScript file for opencode, `pytest`.

**Spec:** `docs/harnesses.md` (capability matrix and sources, written in Task 5). Earlier design: `docs/superpowers/specs/2026-11-02-ml-referee-design.md`.

## Global constraints

- Hook modules import the standard library only.
- The guard stays fail-closed on payloads it cannot parse. A deny uses the harness's documented deny path.
- Existing Claude Code behavior and tests stay unchanged.
- The installer never overwrites a user's hook file. It merges by a marker and is idempotent.
- Docs follow the Google developer documentation style guide. Hooks are a policy layer, not a sandbox.
- The gate before every commit: `.venv/bin/ruff check . && .venv/bin/mypy src && .venv/bin/python -m pytest -q`.

## Harness contracts (from vendor docs, 2026-11-02)

| Harness | Hook file | Payload keys | Shell tool | File tools | Deny contract |
|---|---|---|---|---|---|
| Claude Code | plugin `hooks/hooks.json` | `tool_name`, `tool_input`, `cwd` | `Bash` (`command`) | `Read`, `Write`, `Edit`, `Grep`, `Glob` | stdout `hookSpecificOutput.permissionDecision = "deny"` |
| Codex | `.codex/hooks.json` or `config.toml`; hooks need trust via `/hooks` | `tool_name`, `tool_input`, `cwd`, `turn_id`, `tool_response` | `Bash` (`command`) | `apply_patch` (`command` holds the patch), MCP `mcp__server__tool` | exit 2 with stderr, or the same JSON as Claude Code |
| Devin for Terminal | `.devin/hooks.v1.json` (no wrapper key); also reads `.claude/settings.json` | `tool_name`, `tool_input`, `session_id` | `exec` (`command`) | `read`, `write`, `edit`, `apply_patch`, `grep`, `glob` (field names undocumented) | exit 2, or stdout `{"decision":"block"}`; any other failure fails open |
| Antigravity `agy` | `.agents/hooks.json` or `~/.gemini/config/hooks.json`; plugin `hooks.json` | `toolCall.name`, `toolCall.args`, `workspacePaths`, `error` (post) | `run_command` (`CommandLine`, `Cwd`) | `view_file` (`AbsolutePath`), `write_to_file` (`TargetFile`, `CodeContent`), `replace_file_content` (`TargetFile`, `TargetContent`, `ReplacementContent`), `multi_replace_file_content`, `grep_search` (`SearchPath`, `Query`), `find_by_name` (`SearchDirectory`, `Pattern`) | stdout `{"decision":"deny","reason":"..."}` |
| opencode | JavaScript plugin in `.opencode/plugins/` | `input.tool`, `output.args` | `bash` | `read`, `write`, `edit`, `grep`, `glob` (`filePath`) | throw an `Error` in `tool.execute.before` |

## Review focus

1. An unknown tool name with string inputs. Expected: scanned with the substring rules, never silently allowed.
2. A Codex `apply_patch` payload that edits `.ml-referee.toml` or names the sealed store. Expected: deny.
3. A payload with a missing or wrongly typed field. Expected: deny with a reason, exit code per harness.
4. Running the installer twice, or over an existing user hook file. Expected: no duplicate entries, user hooks kept.
5. A harness payload where the exit code is absent (Codex `PostToolUse`). Expected: the trial is still recorded, `exit_code` is `None`.

---

### Task 1: Event normalization and decision formatting

**Files:**
- Create: `src/ml_referee/harness.py`, `tests/test_harness.py`, `tests/fixtures/harness/` (one JSON fixture per harness and tool)

**Interfaces:**
- Produces:
  - `HARNESSES: tuple[str, ...]` = `("claude", "codex", "devin", "agy", "opencode")`
  - `detect_harness(raw: dict[str, object], environ: Mapping[str, str]) -> str`
  - `normalize(harness: str, raw: dict[str, object]) -> list[dict[str, object]]` returns canonical events (`tool_name` in `Bash`, `Read`, `Write`, `Edit`, `Grep`, `Glob`, `Other`; `tool_input`; `cwd`; `hook_event_name`; optional `exit_code`; `failed`). Raises `ValueError` on unusable payloads.
  - `format_decision(harness: str, allow: bool, reason: str) -> tuple[str, str, int]` returns `(stdout, stderr, exit_code)`.

- [ ] **Step 1: Write the failing tests** from documented payloads: Bash command mapping for all five harnesses; `Read`, `Write`, `Edit`, `Grep`, `Glob` mapping for Claude Code, Devin, `agy`, and opencode; Codex `apply_patch` yields one `Write` event per patch file header plus the patch text as `content`; an unknown tool yields `Other` with all string values kept; malformed input raises `ValueError`; `format_decision` deny contract per harness; allow prints nothing and exits 0.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest -q tests/test_harness.py`. Expected: FAIL.
- [ ] **Step 3: Implement** `harness.py` with the signatures above.
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add harness payload adapters`.

### Task 2: Guard and recorder entry points

**Files:**
- Modify: `src/ml_referee/hooks/guard.py`, `src/ml_referee/hooks/recorder.py`
- Test: `tests/test_guard.py`, `tests/test_recorder.py`, `tests/test_harness_hooks.py`

**Interfaces:**
- Consumes: `normalize`, `format_decision`, `detect_harness`.
- Produces: `main(argv: list[str] | None = None) -> int` in both modules, with `--harness NAME` (default: auto-detect). The guard denies when any canonical event is denied. The recorder takes `exit_code`/`failed` from the canonical event. Exit codes follow `format_decision`.

- [ ] **Step 1: Write the failing tests** that pipe each harness fixture through `python -m ml_referee.hooks.guard --harness NAME` and `recorder`, assert the harness-specific deny output and exit code for a store-touching command, assert allow output for `python train.py`, assert a Codex `apply_patch` that edits `.ml-referee.toml` is denied, assert a Codex `PostToolUse` without an exit code is still recorded.
- [ ] **Step 2: Run the new tests.** Expected: FAIL.
- [ ] **Step 3: Refactor `main()`** in both modules to normalize first. Keep the Claude Code path byte-identical in output.
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: make hooks harness-aware`.

### Task 3: Installer

**Files:**
- Create: `src/ml_referee/install.py`, `tests/test_install.py`
- Modify: `src/ml_referee/cli.py` (new `install` subcommand)

**Interfaces:**
- Consumes: `HARNESSES`.
- Produces: `install(harness: str, root: Path, *, dry_run: bool = False, python: str | None = None) -> list[Change]`, `@dataclass(frozen=True) class Change: path: Path; action: str` (`"create"`, `"merge"`, `"unchanged"`). CLI: `ml-referee install --harness {claude,codex,devin,agy,opencode,all} [--root DIR] [--dry-run]`.
  - Codex writes `<root>/.codex/hooks.json`. Devin writes `<root>/.devin/hooks.v1.json`. `agy` writes `<root>/.agents/hooks.json` under a top-level `ml-referee` key. opencode copies `integrations/opencode/ml-referee.js` to `<root>/.opencode/plugins/`. Claude Code prints the `claude --plugin-dir` instruction and changes nothing.
  - Every entry is tagged with the string `ml-referee` in its `command` so a rerun detects it.

- [ ] **Step 1: Write the failing tests** for each harness in a `tmp_path`: file created with the documented shape and the guard on `PreToolUse` and the recorder on `PostToolUse`; second run reports `unchanged`; existing user hooks in the file are preserved; `--dry-run` writes nothing; invalid existing JSON raises an error without writing.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest -q tests/test_install.py`. Expected: FAIL.
- [ ] **Step 3: Implement** `install.py` and the `install` subcommand.
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add multi-harness installer`.

### Task 4: opencode plugin and agent instructions

**Files:**
- Create: `integrations/opencode/ml-referee.js`, `src/ml_referee/agents_md.py`, `tests/test_opencode_plugin.py`, `tests/test_agents_md.py`
- Modify: `src/ml_referee/install.py` (write the `AGENTS.md` block)

**Interfaces:**
- Produces: `render_block() -> str` and `apply_block(path: Path) -> Change` in `agents_md.py`. The block sits between `<!-- ml-referee:start -->` and `<!-- ml-referee:end -->` and tells the agent never to read, copy, or reference the sealed store or `.ml-referee.toml`, and to ask the person to run `ml-referee claim`. The opencode plugin exports `MlReferee`, whose `tool.execute.before` spawns `python3 -m ml_referee.hooks.guard --harness opencode` with a JSON payload and throws `Error(reason)` on exit 2.

- [ ] **Step 1: Write the failing tests.** For the plugin, run it under `node` with a fake input and assert it throws on a store-touching command and returns on `python train.py` (skip if `node` is missing). For `AGENTS.md`, assert the block is created, replaced in place on rerun, and leaves the rest of the file untouched.
- [ ] **Step 2: Run the new tests.** Expected: FAIL.
- [ ] **Step 3: Implement** the plugin and `agents_md.py`. The installer writes the block for `codex`, `devin`, `agy`, and `opencode`.
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: add opencode plugin and AGENTS.md block`.

### Task 5: Live checks and documentation

**Files:**
- Create: `docs/harnesses.md`, `scripts/live-check-codex.sh`, `scripts/live-check-agy.sh`
- Modify: `README.md`, `docs/architecture.md`, `docs/data_flow.md`, `docs/research/threat-model/threat-model.md`, `docs/research/paper/ml-referee.md` (Limitations only)

- [ ] **Step 1: Run the live checks** on the installed harnesses (Codex and `agy`) in a temporary sealed project. Record the result per harness as verified live, verified against vendor docs only, or failed.
- [ ] **Step 2: Write `docs/harnesses.md`** with the capability matrix, install commands, trust steps, fail modes, and the verification status of each harness.
- [ ] **Step 3: Update** the README, architecture, data flow, threat model, and paper limitations.
- [ ] **Step 4: Run the gate.** Expected: PASS.
- [ ] **Step 5: Commit** `docs: document multi-harness support`.
