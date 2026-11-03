---
description: Seal your holdout data and evaluator before training.
allowed-tools: Bash(ml-referee:*)
---

Run `ml-referee seal $ARGUMENTS` with your `--evaluator` path. This moves your original data into the sealed store and leaves only non-holdout rows in your training file. You do not get a validation split. You see only the holdout row count.

When you are ready to make a claim, run `/ml-claim` yourself. Hooks are a policy layer, not a sandbox.
