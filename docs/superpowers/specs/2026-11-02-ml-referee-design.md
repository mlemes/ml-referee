# ml-referee design

This document defines what `ml-referee` is and why. The implementation plan is at `docs/superpowers/plans/2026-11-02-ml-referee.md`. The research framing is at `docs/research/paper/ml-referee.md`.

## Intent

Coding agents run machine learning experiments and report the result. Recent studies show that agents game the evidence they report [1, 2]. `ml-referee` is a Claude Code plugin that separates the agent that produces a result from the evidence that certifies it.

## Goals

- Seal the holdout data and the evaluator outside the agent's workspace.
- Deny tool calls that edit the evaluator or touch the holdout.
- Record every train and evaluation run in a tamper-evident ledger.
- Audit the data for leakage with deterministic checks.
- Issue a claim only after a sealed evaluation within a holdout budget, adjusted for the trial count.
- Provide a reproducible evaluation that supports a research paper.

## Non-goals

- Replace MLflow, Weights & Biases, or any tracking tool.
- Run or manage training jobs.
- Provide an operating system sandbox in v1.
- Detect every possible cheat.

## Anti-goals

- A plugin that gives false assurance. A claim must state the limits of the guard.
- A plugin that blocks normal work. The guard must not deny legitimate training and evaluation on the validation split.
- A plugin that slows each tool call noticeably. The target is a median hook latency below 100 ms.

## Constraints

- Python 3.11 or later.
- Hooks use the standard library only.
- No network access at run time.
- The package is `ml_referee`. The command is `ml-referee`. The plugin name is `ml-referee`.
- Documents follow the Google developer documentation style guide.
- The repository is private until the person decides to publish.

## Decisions

- The sealed store lives at `${XDG_DATA_HOME:-~/.local/share}/ml-referee/<project-id>/`.
- The ledger is JSONL. Each record holds the SHA-256 of the previous record.
- The configuration file is `.ml-referee.toml` in the project root.
- The default holdout budget is 1.
- The claim adjusts validation p-values with Bonferroni by default and Benjamini-Hochberg on request.
- The reviewer subagent runs on a model different from the one that wrote the code.

## Left to the builder

- Exact regular expressions for the shell command scanner.
- The leakage thresholds, within the ranges in the plan.
- Report layout, within the sections listed in the plan.

## Open items for the person

These are choices made without your input. Change any of them before the plan runs.

1. **Name.** `ml-referee` is a working name.
2. **Stack.** Python was chosen for the machine learning audience and for fast hook startup with the standard library.
3. **Licence.** None yet. The repository is private.

## References

[1] Huang et al., arXiv:2609.28614.
[2] Prasad et al., arXiv:2608.30724.
