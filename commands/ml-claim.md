---
description: Generate your final claim from a budgeted holdout evaluation.
allowed-tools: Bash(ml-referee:*)
---

Run `ml-referee claim $ARGUMENTS` with `--predictions` pointing to your prediction file. You can supply `--val-scores` with a JSON list of validation scores, `--p-value`, and `--out` for your Markdown report. A claim consumes one holdout evaluation from your budget, even if you previously ran `eval`.

You, the person, must run `/ml-claim` when you are ready. Do not ask an agent to invoke it autonomously. Hooks are a policy layer, not a sandbox.
