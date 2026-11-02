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
| A1 | Evaluator tampering | Edit `eval.py` to return a higher score | Evaluator lives in the sealed store. The guard denies edits. A manifest hash is checked at claim time. | Shell route around the guard (see Limitations) |
| A2 | Holdout read | `cat` the holdout labels, then fit to them | The guard denies `Read` and `Bash` commands that touch the store, including globs, symlinks, and `cd`. | Obfuscated shell commands |
| A3 | Train-test leakage | Duplicate rows or shared entities across the split | `/ml-audit` checks duplicates and entity overlap. | Near-duplicates below the threshold |
| A4 | Label distillation | Hard-code rules derived from leaked answers | Audit flags target-leaking features. Reviewer subagent reads the diff. | Subtle disguise. Reviewers can miss it [1]. |
| A5 | Holdout reuse | Repeated holdout looks tune the model to it | Holdout budget (default 1). `eval` refuses after the budget. | Budget set too high by the person |
| A6 | Ledger tampering | Delete or edit failed trials | Hash chain. `verify` fails on any break. `claim` refuses. | Truncating the tail if the head hash is not stored elsewhere |
| A7 | Unlogged trials | Run training with a command outside the patterns | Pattern list in config. Audit compares file modification times with ledger entries. | Commands the patterns miss |
| A8 | Metric substitution | Report a different metric than the sealed one | The claim report takes the metric from the manifest only. | None known |

## Fail-closed rule

If the guard cannot parse a tool call or its configuration, it denies the call and prints the reason. This can interrupt a session. The person fixes the configuration and retries.

## Reference

[1] Huang et al., arXiv:2609.28614. The study reports that an LLM review panel missed 6.5% of confirmed exploits.
