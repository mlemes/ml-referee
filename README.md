# ml-referee

A shared Python core with Claude Code and Codex integrations that separates the agent that produces a machine learning result from the evidence that certifies it.

**Status:** implemented, v0.1. The scripted-attack bench exists. Agent runs for the paper are pending, and the paper has no results yet.

## Install the plugin locally

1. Clone this repository and create the environment:

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -e ".[dev]"
   source .venv/bin/activate
   ```

2. Keep that environment active, change to your ML project, and load this repository as the Claude Code plugin:

   ```bash
   cd /path/to/your-ml-project
   claude --plugin-dir /path/to/ml-referee
   ```

3. In your project, create `.ml-referee.toml`. From your own terminal, seal the data with `ml-referee seal --evaluator /path/to/evaluator.py` before training. `/ml-referee:ml-seal` explains this owner-only command; it does not execute it through the agent.

The Claude hooks run `python3`, which must find the installed package and dependencies. Keep the environment active when launching Claude. A hook interpreter/import failure can leave the guard inactive; check a disposable protected-read attempt before relying on a new installation.

## Use the same checkout with Codex

With the same environment active, run these commands from your ML project:

```bash
ml-referee install --harness codex
codex
```

Trust the project, open `/hooks`, and review and trust the installed guard and recorder. Installation does not grant hook trust. Re-review hooks after their definitions change. The installer writes `.codex/hooks.json` and a bounded `AGENTS.md` block; the Python implementation is shared with Claude Code.

Both agents may run `ml-referee audit`, `ml-referee eval`, and `ml-referee verify-ledger`. You run `ml-referee seal` and the final `ml-referee claim --predictions /path/to/predictions.csv --out claim.md` from your own terminal. The guard deliberately denies those owner-only operations through agent tools. Claude's `/ml-referee:ml-claim` provides guidance, not an execution exception.

See [live checks](docs/research/evaluation/live-checks.md) for the versions, execution conditions, and limits actually verified.

## Supported harnesses

The guard also runs in Codex, Devin for Terminal, Antigravity `agy`, and opencode. Install the hook files for every harness at once, from the project root:

```bash
ml-referee install --harness all
```

Pass a single harness name, such as `--harness codex`, to install only that one. The installer writes hook files and an `AGENTS.md` block, and it never overwrites your existing hooks. Codex needs you to trust the hooks in `/hooks`. See `docs/harnesses.md` for the capability matrix, fail modes, and verification status.

## Run the gate

```bash
.venv/bin/ruff check . && .venv/bin/mypy src && .venv/bin/python -m pytest -q
```

## Run the scripted attacks

```bash
.venv/bin/python -m bench.run_attacks
```

## Limits

- Hooks are a policy layer, not a sandbox. An agent with unrestricted shell access can route around them.
- The audit finds exact duplicates, near duplicates, shared entities, near-perfect label proxies, and fit before split. The near-duplicate check compares standardized numeric features and flags only holdout rows that sit closer to a training row than almost all training rows sit to each other, so a near duplicate with large noise stays undetected.
- Audit and claim counts reveal holdout membership to an agent that can run the CLI.
- Codex may omit process status from post-tool events. Such trials have `exit_code: null` and `failed: null`, meaning unknown; they still count as attempts. Older records with a null exit code and `failed: false` do not prove success.
- The guard lets the agent run `ml-referee audit`, `eval`, and `verify-ledger` inside ordinary pipelines and redirections, and denies `seal`, `claim`, and `install`. `ml-referee eval --predict-cmd "CMD"` scores a predictor on the sealed rows without showing it their labels. The predictor runs as the same user and sees the unlabeled sealed features.

## Read first

- Paper draft: `docs/research/paper/ml-referee.md`
- Design: `docs/superpowers/specs/2026-11-02-ml-referee-design.md`
- Plan: `docs/superpowers/plans/2026-11-02-ml-referee.md`
- Architecture: `docs/architecture.md`
- Data flow: `docs/data_flow.md`

## Repository layout

```text
src/ml_referee/   package: config, ledger, seal, policy, runner, cli, hooks, audit, claim
hooks/            hooks.json registration
commands/         /ml-seal, /ml-audit, /ml-claim
skills/           ml-referee skill
agents/           ml-reviewer agent
bench/            scripted attacks
tests/            test suite
docs/
  architecture.md
  data_flow.md
  research/       paper, references, related work, threat model, evaluation
  superpowers/    specs and plans
```
