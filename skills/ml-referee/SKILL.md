---
name: ml-referee
description: Keep your holdout sealed, audit leakage, and prepare a human-run final claim.
---

Use this workflow when you train models with a sealed holdout. Work only with your workspace training data and source files. Never read, copy, or reference the sealed store or `.ml-referee.toml`. Never attempt to bypass the guard or access holdout rows through another tool.

Use `/ml-audit` to inspect leakage findings. Review summary counts and severities without exposing sealed data. Ask the person to run `/ml-claim` when their predictions are ready. Never run the final claim autonomously.

Hooks are a policy layer, not a sandbox. You cannot treat them as operating system isolation or proof that a tool cannot bypass policy.
