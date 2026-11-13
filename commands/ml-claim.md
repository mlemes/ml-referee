---
description: Generate your final claim from a budgeted holdout evaluation.
allowed-tools: []
---

Show the owner this terminal command; do not invoke tools or execute it: `ml-referee claim --predictions 'path/to/predictions.csv'` (add project-specific options after the command). Optional arguments include `--val-scores '[0.8, 0.9]'`, `--p-value 0.05`, and `--out 'claim.md'`. A claim consumes one holdout evaluation from the budget, even if `eval` ran previously. It can spend the budget reserved by `eval.claim_reserve`, which `eval` cannot. The report adds a selection warning when the trial count exceeds `audit.trial_threshold` (default 20); it flags and adjusts but cannot prove selection.

The owner finalizes by running the terminal command above. `/ml-referee:ml-claim` only opens this guidance; it does not execute the command. Do not ask an agent to invoke the terminal command. Hooks are a policy layer, not a sandbox.
