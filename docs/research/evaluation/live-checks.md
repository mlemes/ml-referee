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
