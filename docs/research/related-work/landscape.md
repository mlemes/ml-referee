# Related tooling landscape

This file lists existing Claude Code skills and servers for machine learning work. It was compiled on 2026-11-02 from web search results. Most entries come from third-party directories, so check each repository for activity before you rely on it.

## Existing tools

| Area | Tool | Source | What it does | Integrity enforcement |
|---|---|---|---|---|
| Tracking | MLflow skill | [claudemarketplaces.com](https://claudemarketplaces.com/skills/davila7/claude-code-templates/mlflow) | Experiment tracking, model registry, deployment | None |
| Tracking | MLflow MCP server | [claudemarketplaces.com](https://claudemarketplaces.com/mcp/io.github.us-all/mlflow) | 82 tools for experiments, runs, registry, traces | None |
| Tracking | Weights & Biases skill | [claudemarketplaces.com](https://claudemarketplaces.com/skills/orchestra-research/ai-research-skills/weights-and-biases) | Runs and sweeps | None |
| Tracking | TensorBoard skill | [claudemarketplaces.com](https://claudemarketplaces.com/skills/orchestra-research/ai-research-skills/tensorboard) | Run visualization | None |
| Training | huggingface/skills | [GitHub](https://github.com/huggingface/skills) | Trainer, datasets, evaluation, jobs | None |
| Loop | autoresearch (Karpathy) | [GitHub](https://github.com/karpathy/autoresearch) | Agent edits `train.py`, trains, keeps or discards | None. The agent can edit the evaluator. |
| Loop | autoresearch crate | [docs.rs](https://docs.rs/crate/autoresearch/latest) | Installs the loop into several agents | None |
| Loop | autoresearch-claude-code | [sourcepulse.org](https://www.sourcepulse.org/projects/27855479) | Logs to `autoresearch.jsonl`, commits wins | None |
| Loop | supercent-io autoresearch | [claudemarketplaces.com](https://claudemarketplaces.com/skills/supercent-io/skills-template/autoresearch) | Ratcheting GPU experiment loop | Ratchet only. Does not protect the metric. |
| General | ml-engineer, mlops-workflows | [ml-engineer](https://claudemarketplaces.com/skills/404kidwiz/claude-supercode-skills/ml-engineer), [mlops-workflows](https://claudemarketplaces.com/skills/manutej/luxor-claude-marketplace/mlops-workflows) | Generic checklists | None |

## Gap

No tool in this list separates the agent that produces a result from the evidence that certifies it. `ml-referee` targets that gap. It complements the tracking and loop tools above.

## Maintenance rule

Re-run the search before each paper revision. Add a row for every new tool and record the date.
