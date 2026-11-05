---
name: ml-referee
description: Keep your holdout sealed, audit leakage, and leave the final claim to the person.
---

Use this workflow when you train models with a sealed holdout. Work only with your workspace training data and source files. Never read, list, copy, or reference the sealed store, and never edit `.ml-referee.toml`. Never attempt to bypass the guard or access holdout rows through another tool.

Commands you may run, alone or in ordinary pipelines and redirections:

- `ml-referee audit [--against PATH ...] [--sources PATH ...]` prints leakage findings as JSON lines. Exit code 3 means a high-severity finding.
- `ml-referee eval --predictions PATH` scores a predictions file for the sealed rows. You cannot see those rows, so a person must supply that file.
- `ml-referee eval --predict-cmd "COMMAND"` runs COMMAND with `{features}` (the sealed rows without labels) and `{out}` (a one-column predictions CSV to write) and prints only the score and the row count.
- `ml-referee verify-ledger` checks the evaluation ledger.

Sealed evaluations are limited by the holdout budget, which `AGENTS.md` shows when it is known. A predict command that fails before scoring spends none. The person produces the final claim with `/ml-claim`. `seal`, `claim`, and `install` are not for the agent.

Hooks are a policy layer, not a sandbox. You cannot treat them as operating system isolation or proof that a tool cannot bypass policy.
