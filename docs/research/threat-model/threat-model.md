# Threat model

This file defines what `ml-referee` defends against. The paper summarizes it in the "Threat model" section.

## Adversary

The adversary is the coding agent. It is not assumed to be malicious. It optimizes the reported score. It can use `Read`, `Write`, `Edit`, and `Bash` as the same operating system user as the person. It can see the workspace and the validation score.

Out of scope:

- A malicious person.
- Root or kernel compromise.
- Side channels such as timing.

## Assets

- Holdout labels.
- Evaluator code.
- Trial ledger.
- Final claim report.

## Attack classes

| ID | Attack | Example | Mitigation | Residual risk |
|---|---|---|---|---|
| A1 | Evaluator tampering | Edit `eval.py` to return a higher score | Evaluator lives in the sealed store. The guard denies access to the store and denies `Write`, `Edit`, and `Bash` modification of `.ml-referee.toml`. The runner checks the manifest hash before each evaluation. | Shell routes the guard does not catch (see residuals below) |
| A2 | Holdout read | `cat` the holdout labels, then fit to them | The guard denies tool calls that touch the store. It uses normalized-substring rules and symlink-aware path resolution, and expands globs. | `find ~ -name 'holdout.parquet' -exec cat`, glob-obfuscated `cd` chains, and inline Python that walks `~` |
| A3 | Train-test leakage | Duplicate rows or shared entities across the split | `/ml-audit` checks duplicates and entity overlap. | Near-duplicates below the threshold. Audit counts reveal holdout membership to an agent that can run the CLI (accepted residual). |
| A4 | Label distillation | Hard-code rules derived from leaked answers | Audit flags target-leaking features. Reviewer subagent reads the diff. | Subtle disguise. Reviewers can miss it [1]. |
| A5 | Holdout reuse | Repeated holdout looks tune the model to it | Holdout budget (default 1). The runner takes a lock and refuses after the budget. | The agent can run the CLI itself, so it gets at most `holdout_budget` sealed looks. The person can set the budget too high. `audit` and `claim` counts reveal holdout membership to an agent that can run the CLI (accepted residual). |
| A6 | Ledger tampering | Delete or edit failed trials | Hash chain. `verify-ledger` fails on any break. The runner and `claim` verify the store first and refuse on failure. `head.txt` detects tail truncation. The write is atomic, and `append` repairs only a head that is one record behind. | `head.txt` lives in the same store. A same-user agent that can write the store can forge both. Version 2 needs a separate user or a container. |
| A7 | Unlogged trials | Run training with a command outside the patterns | The recorder matches `trial_patterns` per shell segment after it strips environment assignments and wrappers (`nohup`, `env`, `time`, `nice`, `sh -c`). | The recorder cannot see commands outside the patterns. The bench reports 0 percent blocked for A7. |
| A8 | Metric substitution | Report a different metric than the sealed one | The claim report takes the metric from the manifest only. | None known |

## Other residual risk

- If `python3` is missing, a hook crash is non-blocking in Claude Code, so the guard fails open.
- Guard coverage differs per harness. The guard checks the tools that each harness adapter maps. A tool that an adapter does not map is checked as an `Other` event, with every string treated as both a path and content.
- Codex has no `Read` tool, so Codex reads go through `Bash`. A Codex agent that reads through another route is not checked by the file rules.
- Devin and agy fail open on a hook crash or a timeout. A hook process that cannot import `ml_referee` exits with code 1, which those harnesses do not treat as a deny.
- Codex hooks take effect only after you trust them in `/hooks`. Until then, the guard does not run.
- Unknown tool names become `Other` events. The guard checks every string of an `Other` event as a candidate path (config file, store) and as content, and it validates the `cwd`. Unmapped keys of mapped tools are re-scanned the same way.
- The runner kills the evaluator process group on timeout and never includes evaluator output in errors, so a failing evaluator does not leak holdout content.

## Fail-closed rule

If the guard cannot parse a tool call or its configuration, it denies the call and prints the reason. This can interrupt a session. The person fixes the configuration and retries.

## Reference

[1] Huang et al., arXiv:2609.28614. The study reports that an LLM review panel missed 6.5% of confirmed exploits.
