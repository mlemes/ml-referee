---
description: Check your training data and source files for leakage.
allowed-tools: Bash(ml-referee:*)
---

Run `ml-referee audit $ARGUMENTS`. Use `--sources` to include your training scripts and `--against` to compare the training data with a file you can see, such as a public test set. You receive JSON lines with check names, severities, and summary details. Exit code 3 means a high-severity finding needs your attention. The command may sit in a pipeline or have its output redirected to a file outside the sealed store.

The person runs `/ml-claim` when they are ready to make a claim. Hooks are a policy layer, not a sandbox.
