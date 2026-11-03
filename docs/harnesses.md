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
| Claude Code | Nothing. The installer prints the `claude --plugin-dir` command. | Load the plugin with `--plugin-dir`. | Guard fails closed on unparsable payloads. | Tested through a subprocess, and the plugin passes `claude plugin validate`. |
| Codex | `.codex/hooks.json`, `AGENTS.md` block | Run `/hooks` and trust the entries. | Guard fails closed. | Not verified live. The runbook barred Codex use while its weekly quota was capped. |
| Devin | `.devin/hooks.v1.json`, `AGENTS.md` block | None documented. | Fails open on hook crash or timeout. | Vendor docs only. Not installed. |
| agy | `.agents/hooks.json`, `AGENTS.md` block | Not checked. The live runs did not test a trust prompt. | Fails open on hook crash or timeout. | Verified live on agy 1.4.12. The pre-tool hook denied a store read, and a benign command ran. See `docs/research/evaluation/live-checks.md`. |
| opencode | `.opencode/plugins/ml-referee.js`, `AGENTS.md` block | None documented. | Plugin throws on deny. A spawn error throws, so the call is blocked. | Vendor docs only. Not installed. The runtime issue in anomalyco/opencode#25918 is unconfirmed, and the hook may not fire. |

## Known limits

- Guard coverage differs per harness. Codex has no `Read` tool, so Codex reads go through `Bash`.
- Devin and agy fail open when a hook crashes or times out.
- Codex hooks do nothing until you trust them in `/hooks`.
- Unknown and unmapped tools (MCP tools, for example) become `Other` events. The guard checks every string as a candidate path (config file, store) and as content, and it validates the event `cwd`. The adapter also re-scans any string key it does not map, such as extra or shadowed aliases.
- Hooks are a policy layer, not a sandbox. An agent with unrestricted shell access can route around them.
- The hook command runs the Python interpreter that the installer records. That interpreter must be able to import `ml_referee`. If it cannot, the hook exits with code 1 and the harness can fail open.

## Sources

- Codex hooks: https://learn.chatgpt.com/docs/hooks
- Devin hooks: https://docs.devin.ai/cli/extensibility/hooks/overview.md
- agy hooks: https://www.antigravity.google/docs/hooks?tab=cli
- agy plugins: https://www.antigravity.google/docs/plugins?tab=cli
- opencode plugins: https://opencode.ai/en/docs/plugins
- opencode runtime issue: https://github.com/anomalyco/opencode/issues/25918
