---
description: Check your training data and source files for leakage.
allowed-tools: Bash(ml-referee:*)
---

Run `ml-referee audit $ARGUMENTS`. Use `--sources` to include your training scripts. You receive JSON lines with check names, severities, and summary details. Exit code 3 means a high-severity finding needs your attention.

When you are ready to make a claim, run `/ml-claim` yourself. Hooks are a policy layer, not a sandbox.
