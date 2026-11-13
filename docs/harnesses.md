# Supported harnesses

ml-referee guards the sealed store from five agent harnesses. Run `ml-referee install` to write the hook files for a harness, and read this page to learn what each harness checks and how much you can trust it.

## Install hooks for a harness

Run one of the following commands from your project root:

```bash
ml-referee install --harness codex
ml-referee install --harness devin
ml-referee install --harness agy
ml-referee install --harness opencode
ml-referee install --harness claude
ml-referee install --harness all
```

Add `--dry-run` to list the changes without writing them. The installer merges its entries into any existing hook file, tags each entry with `ml-referee`, and leaves your own hooks in place. A second run reports `unchanged`.

## Capability matrix

| Harness | Hook file | Payload keys | Shell tool | File tools | Deny contract |
|---|---|---|---|---|---|
| Claude Code | Plugin `hooks/hooks.json` | `tool_name`, `tool_input`, `cwd` | `Bash` (`command`) | `Read`, `Write`, `Edit`, `Grep`, `Glob` | stdout `hookSpecificOutput.permissionDecision = "deny"` |
| Codex | `.codex/hooks.json` or `config.toml`. Hooks need trust through `/hooks`. | `tool_name`, `tool_input`, `cwd`, `turn_id`, `tool_response` | `Bash` (`command`) | `apply_patch` (`command` holds the patch), MCP `mcp__server__tool` | Exit 2 with stderr, or the same JSON as Claude Code |
| Devin for Terminal | `.devin/hooks.v1.json` (no wrapper key). Also reads `.claude/settings.json`. | `tool_name`, `tool_input`, `session_id` | `exec` (`command`) | `read`, `write`, `edit`, `apply_patch`, `grep`, `glob`. Field names are undocumented. | Exit 2, or stdout `{"decision":"block"}`. Any other failure fails open. |
| Antigravity `agy` | `.agents/hooks.json` or `~/.gemini/config/hooks.json`. Plugin `hooks.json`. | `toolCall.name`, `toolCall.args`, `workspacePaths`, `error` (post) | `run_command` (`CommandLine`, `Cwd`) | `view_file` (`AbsolutePath`), `write_to_file` (`TargetFile`, `CodeContent`), `replace_file_content` (`TargetFile`, `TargetContent`, `ReplacementContent`), `multi_replace_file_content`, `grep_search` (`SearchPath`, `Query`), `find_by_name` (`SearchDirectory`, `Pattern`) | stdout `{"decision":"deny","reason":"..."}` |
| opencode | JavaScript plugin in `.opencode/plugins/` | `input.tool`, `output.args` | `bash` | `read`, `write`, `edit`, `grep`, `glob` (`filePath`) | Throw an `Error` in `tool.execute.before` |

## Harness details

The following table lists the install output, the trust step, the fail mode, and the verification status for each harness.

| Harness | Install writes | Trust step | Fail mode | Verification status |
|---|---|---|---|---|
| Claude Code | Nothing. The installer prints the `claude --plugin-dir` command. | Load the plugin with `--plugin-dir`. | Guard fails closed on unparsable payloads; interpreter/import failure is not a deny. | Live guard, training, budget and ledger checks on 2.1.323; local plugin validation passes. Sandbox conditions are recorded in `docs/research/evaluation/live-checks.md`. |
| Codex | `.codex/hooks.json`, `AGENTS.md` block | Trust the project, then run `/hooks` and trust the entries. | Guard fails closed when loaded and running. | Live guard, training, budget and ledger checks on 0.167.1 using reviewed one-off hook trust. Persisted `/hooks` UI trust was not exercised. See `docs/research/evaluation/live-checks.md`. |
| Devin | `.devin/hooks.v1.json`, `AGENTS.md` block | None documented. | Fails open on hook crash or timeout. | Vendor docs only. Not installed. |
| agy | `.agents/hooks.json`, `AGENTS.md` block | Not checked. The live runs did not test a trust prompt. | Fails open on hook crash or timeout. | Verified live on agy 1.4.12. The pre-tool hook denied a store read, and a benign command ran. See `docs/research/evaluation/live-checks.md`. |
| opencode | `.opencode/plugins/ml-referee.js`, `AGENTS.md` block | None documented. | Plugin throws on deny. A spawn error throws, so the call is blocked. | Vendor docs only. Not installed. The runtime issue in anomalyco/opencode#25918 is unconfirmed, and the hook may not fire. |

## What the agent can run

On every harness the guard treats file content as data. A `Write`, `Edit`, or `apply_patch` body, and a quoted `cat <<'EOF'` or `tee` heredoc body, may mention `ml-referee` or `.ml-referee.toml` (a `results.md` that cites `ml-referee audit`); the same text as a path into the store (`ml-referee/...`, the store path, `.local/share`) is still denied. A bare `*` in a command or heredoc (`0.5 * a`, `grep -r x * | wc -l`) is allowed, while `.*`, `.m*`, `[.]ml*` and any use after `shopt -s dotglob` are not. The tests in `tests/test_guard_false_positives.py` cover the `claude`, `codex`, `devin`, `agy`, and `opencode` payload shapes.

On every harness the guard lets the agent run the plugin CLI, alone or in ordinary shell pipelines and redirections: `ml-referee audit [--against PATH ...] [--sources PATH ...]`, `ml-referee eval --predictions PATH`, `ml-referee eval --predict-cmd "CMD"`, `ml-referee verify-ledger`, and `ml-referee <subcommand> --help`. Examples that pass: `ml-referee audit --against test.csv 2>&1 | tail -n 40`, `timeout 110 ml-referee audit > audit.log 2>&1`, `which ml-referee && ml-referee audit`, `(command -v ml-referee && ml-referee audit) > audit.log 2>&1`. The guard still denies `seal`, `claim`, and `install`; any path or redirection target inside the store or at `.ml-referee.toml`; `$`, backticks, and braces in CLI paths; command substitution, here documents, and `bash -c` or `eval` that carry the CLI name. The `AGENTS.md` block lists these commands and the holdout budget (printed from `.ml-referee.toml` when the config exists at install time). It describes what exists and does not tell the agent to run it.

`ml-referee eval --predict-cmd "python predict.py {features} {out}"` lets an agent that cannot see the sealed rows get a score: the runner hands the command the sealed rows without labels and scores the file it writes. See `docs/architecture.md` for the checks and the residual (the predictor runs as the same user).

## Known limits

- Guard coverage differs per harness. Codex has no `Read` tool, so Codex reads go through `Bash`.
- Devin and agy fail open when a hook crashes or times out.
- Codex hooks do nothing until you trust them in `/hooks`.
- Codex 0.167.1 can send stdout alone in `PostToolUse.tool_response`, without process status. New trial records use `exit_code: null, failed: null` for that unknown outcome. An explicit integer exit code or failure event preserves a known outcome. Stdout cannot establish success or failure. Historical records are not migrated; a null exit code in an older record is not proof of success even if `failed` is false.
- Claude `/ml-referee:ml-seal` and `/ml-referee:ml-claim` provide instructions for the person to execute in a terminal. Slash-command invocation does not exempt an agent tool call from the guard. Both hosts intentionally deny agent-driven `seal`, `claim`, and `install`.
- Unknown and unmapped tools (MCP tools, for example) become `Other` events. The guard checks every string as a candidate path (config file, store) and as content, and it validates the event `cwd`. The adapter also re-scans any string key it does not map, such as extra or shadowed aliases.
- Config discovery does not depend on the payload `cwd` alone. The guard uses the harness root when present (`CLAUDE_PROJECT_DIR` for Claude Code, `DEVIN_PROJECT_DIR` for Devin, `workspacePaths` for agy), then payload workspace or root fields, then a walk up from `cwd`. Codex and opencode send only `cwd`, so for them a `cwd` outside the workspace finds no config and gets the config-free store-root rules (deny any call whose resolved `cwd`, path argument, or text names the store root, allow the rest).
- Hooks are a policy layer, not a sandbox. An agent with unrestricted shell access can route around them.
- The hook command runs the Python interpreter that the installer records. That interpreter must be able to import `ml_referee`. If it cannot, the hook exits with code 1 and the harness can fail open.

## Sources

- Codex hooks: https://learn.chatgpt.com/docs/hooks
- Devin hooks: https://docs.devin.ai/cli/extensibility/hooks/overview.md
- agy hooks: https://www.antigravity.google/docs/hooks?tab=cli
- agy plugins: https://www.antigravity.google/docs/plugins?tab=cli
- opencode plugins: https://opencode.ai/en/docs/plugins
- opencode runtime issue: https://github.com/anomalyco/opencode/issues/25918
