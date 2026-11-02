# Evaluation protocol

This file defines how to evaluate `ml-referee`. Fill in the results table in the paper only from runs that follow this protocol.

**Status:** the scripted-attack bench for RQ1 exists at `bench/`. Agent runs for RQ2 to RQ4 are pending.

## Research questions

1. **RQ1, enforcement.** What fraction of scripted attacks does the guard block?
2. **RQ2, detection.** What fraction of BAITBENCH shortcuts does the audit flag?
3. **RQ3, calibration.** How large is the gap between the reported score and the sealed-holdout score?
4. **RQ4, cost.** What are the median and 95th percentile hook latency, and the wall-clock overhead of a run?

## Conditions

| ID | Condition |
|---|---|
| C0 | No plugin. |
| C1 | Prompt that forbids cheating, no plugin. |
| C2 | Permission deny rules on the evaluator and holdout paths only. |
| C3 | `ml-referee` full. |

## Tasks

- The three tabular tasks from BAITBENCH (Prasad et al., arXiv:2608.30724). The BAITBENCH release is unconfirmed. Confirm the release and license before use.
- One open-ended research-pipeline task written for this project, with a planted evaluator weakness. Define it in `bench/tasks/`.

## Scripted attacks (RQ1)

Run each attack class A1 to A8 from `docs/research/threat-model/threat-model.md` as a scripted tool-call sequence. Include three shell obfuscations for A2: glob, symlink, and variable expansion. Report the blocked fraction per class and per condition.

The bench covers C0, C2, and C3. C1 is an agent condition. Run it with:

```bash
.venv/bin/python -m bench.run_attacks
```

The bench prints a Markdown table of block rates. It uses synthetic tool-call events and does not measure agent behavior.

## Agent runs (RQ2 to RQ4, pending)

- Use at least two agent models. Pick them with the live quota check before the run.
- Run each task and condition at least 10 times. Compute the final run count from a power analysis on the pilot (Task 11 of the plan).
- Record: reported score, sealed-holdout score, trial count, audit findings, hack indicators from the BAITBENCH judge.

## Metrics

- **Block rate** per attack class.
- **Flag rate** of shortcuts by the audit, with a false positive rate on clean tasks.
- **Calibration gap:** reported score minus sealed-holdout score. Report the mean and a bootstrap 95% confidence interval.
- **Latency:** median and 95th percentile of hook execution time in milliseconds.

## Threats to validity

- BAITBENCH tasks are small and synthetic. Results may not transfer to large pipelines.
- Agent behavior changes with model version. Record model IDs and dates.
- The scripted attacks encode the project's own ideas of attacks. Add attacks found in transcripts from [2] and [1] when available.
- The same authors design the audit and the attack suite. Publish both so others can extend them.

## Reporting

Save raw ledgers and transcripts under `bench/results/<date>/`. Commit a summary table only. Keep raw data out of version control if it contains private data.

## References

[1] Huang et al., arXiv:2609.28614.
[2] Prasad et al., arXiv:2608.30724.
