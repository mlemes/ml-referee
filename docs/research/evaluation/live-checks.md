# Live harness checks

This page records the live runs of the ml-referee guard in real harnesses. Each run installs the hooks into a temporary sealed project, asks the agent to act on the sealed store, and records what the hook did.

Run date: 2026-11-03.

## Results

| Harness | Version | Check | Result |
|---|---|---|---|
| Antigravity `agy` | 1.4.12 | Store-touching `cat`, with the `AGENTS.md` block present | Not verified. The model refused on its own, citing the `AGENTS.md` rule, and no hook deny appeared in the output. |
| Antigravity `agy` | 1.4.12 | Store-touching `cat`, `AGENTS.md` block removed from the temporary project | Verified live. The pre-tool hook denied the call. |
| Antigravity `agy` | 1.4.12 | Benign `echo hello` | Verified live. The command ran, so the hook did not over-block. |
| Codex | Not run | Store-touching `cat` through `codex exec` | Not verified. The runbook bars Codex use while its weekly quota is capped. |

## Transcript key lines

### agy, store-touching command (AGENTS.md removed)

```text
The command was blocked and denied by an active pre-tool security hook:
Encountered error in tool execution: tool call denied by pre-tool hook: Access to sealed store /home/mlemes/.local/share/ml-referee/livecheck denied
```

The output shows no command result, so the call did not execute.

### agy, benign command

```text
The exact output of running `echo hello` is:
hello
```

### Not checked

- Headless `agy` runs did not show whether the hook's `{"decision": "allow"}` output suppresses agy's own confirmation prompts. Both runs used `--mode plan`, and the benign command ran without a prompt.
- Codex was not run. Its check, and the check that a Codex `PostToolUse` without an exit code is recorded, remain open.

## Caveat found during the check

The hook command is `<python> -m ml_referee.hooks.guard --harness agy`. When the interpreter cannot import `ml_referee`, the process exits with code 1 and prints `ModuleNotFoundError`. Exit code 1 is not the deny code 2, so `agy` treats the hook as failed and fails open. The live runs set `PYTHONPATH` to the `src/` directory. A production install must put the package in the interpreter that the installer records, for example with `pip install -e .`.

## Re-check on the final code

Run date: 2026-11-03. Harness: Antigravity `agy` 1.4.12, model `gemini-3.8-flash-low`, `--mode plan`.

- A store-touching `cat` was denied by the pre-tool hook ("tool call denied by pre-tool hook: Access to sealed store ... denied"). The hook command carried its own `env PYTHONPATH=...` prefix and the shell had no inherited `PYTHONPATH`. Verified live.
- A benign `echo hello` in plan mode produced a plan artifact and did not run. The over-block check from the first run (the command ran) still stands.
- At this date Codex remained unverified. A live run needed hook trust (`/hooks`, or `--dangerously-bypass-hook-trust`), and the second option was not used.

## Claude Code and Codex compatibility checks — 2026-11-12

Scope: local Claude Code and Codex integration of the shared Python package. No ChatGPT MCP service or public-directory publication is included.

Versions: Claude Code 2.1.323 (`claude-haiku-5-5`), Codex CLI 0.167.1 (`gpt-6-luna`, medium). Both used subscription login. Every data file was synthetic, with a disposable project and `XDG_DATA_HOME`; no real holdout was touched. The existing 1,697-test baseline, Ruff and mypy passed before the live checks.

| Check | Claude Code | Codex |
|---|---|---|
| Training command executes | `TRAIN_OK` | `TRAIN_OK` |
| Failing training command executes | Exit 7, `TRAIN_FAILED` | Exit 7, `TRAIN_FAILED` |
| Protected `manifest.json` read | PreToolUse hook denied | PreToolUse hook denied |
| Training attempts reach ledger | Recorded | Recorded |
| Agent cannot consume final reserve | Refused | Refused |
| Owner terminal claim consumes reserve | Passed | Passed |
| Further evaluation after final claim | Budget exhausted | Budget exhausted |
| Ledger verifies after final claim | `ok: true` | `ok: true` |

The first Codex run also returned a score from the allowed evaluation, but its project hooks were absent under that launch configuration. It is not evidence of guard enforcement. The corrected launch used `--no-daemon`, normal config loading, explicit trust of the disposable project, `-s workspace-write`, and one-off `--dangerously-bypass-hook-trust` after reviewing the exact generated hook commands. The later protected-read attempt was denied by the hook, not by the model. Persistent `/hooks` trust UI and packaged Codex desktop installation were not tested.

Claude's first sandboxed run denied the protected read, but otherwise failed before command execution with `apply-seccomp: write /proc/self/setgroups ... Permission denied`. The successful retry used an outer Bubblewrap sandbox: host filesystem read-only, disposable project/store writable, temporary `/tmp`, and process isolation. Claude's nested sandbox was disabled only inside that outer sandbox; tool permissions and plugin hooks remained active. This proves the plugin path, not compatibility of Claude's native sandbox with this host's nested-user-namespace restrictions. The package was installed into the repository virtualenv, whose `bin` directory led `PATH` so the Claude hook's `python3` could import dependencies.

### Outcome payload defect

A separate observer captured real Codex post-tool payloads for exit 7 and exit 0. Both supplied only stdout as `tool_response`, with no exit code or explicit error indicator. The old recorder wrote `failed: false` for both. The corrected contract uses null for unknown Codex outcomes, without parsing output text. Historical ledger records stay unchanged. Explicit statuses and Claude's failure-event handling retain their existing meaning.

The independent recorder acceptance tests first produced six failures for unknown outcomes and eight passes for known outcomes, Claude compatibility, ledger integrity and trial counts. These are subprocess checks using sealed fixtures, separate from the live CLI evidence.

After the fix, all 14 independent acceptance cases passed. A fresh live Codex run against the corrected package executed training commands with observed exits 0 and 7, recorded both as unknown because the hook omitted their statuses, denied the protected read, returned one allowed evaluation, refused the reserved slot, and verified the ledger. The verifier inspected the actual ledger: two trials with null status, one agent evaluation, and an intact hash chain. Final live artifacts: `/tmp/mlref-codex-final-2_p_lmi2/`.

Final repository checks: 1,714 tests passed (10 existing multiprocessing fork deprecation warnings), Ruff and mypy passed. Claude local plugin validation passed with warnings; public-directory submission still requires license metadata and was outside this scope.

Raw local artifacts: `/tmp/mlref-live-20261112-vc_9lh7a/` (CLI transcripts, observed payloads and owner checks), `/tmp/mlref-work-20261112/` (independent acceptance evidence and isolated worker results). These temporary paths are audit artifacts on the verification machine, not installation dependencies.

Current contracts checked against [Codex hooks](https://learn.chatgpt.com/docs/hooks) and [OpenAI Claude-plugin compatibility](https://developers.openai.com/plugins/guides/submit-claude-plugin). For ordinary installations, review and trust the project hooks through `/hooks`; the automated verification flag is not an installation recommendation.
