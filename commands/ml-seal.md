---
description: Seal your holdout data and evaluator before training.
allowed-tools: []
---

Show the owner this terminal command; do not invoke tools or execute it: `ml-referee seal --evaluator 'path/to/evaluator.py'` (add project-specific options after the command). It moves the original data into the sealed store and leaves only non-holdout rows in the training file. There is no validation split; it shows only the holdout row count.

When ready to make a claim, the owner can open `/ml-referee:ml-claim` for guidance. That slash command only opens guidance; it does not run the operation. Hooks are a policy layer, not a sandbox.
