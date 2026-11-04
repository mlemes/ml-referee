# ml-referee

A Claude Code plugin that separates the agent that produces a machine learning result from the evidence that certifies it.

**Status:** implemented, v0.1. The scripted-attack bench exists. Agent runs for the paper are pending, and the paper has no results yet.

## Install the plugin locally

1. Clone this repository and create the environment:

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -e ".[dev]"
   ```

2. Start Claude Code with the plugin from the repository root:

   ```bash
   claude --plugin-dir .
   ```

3. In your project, create `.ml-referee.toml`, then run `/ml-seal`.

The hooks run `python3`. If `python3` is missing, a hook crash does not block the tool call in Claude Code, so the guard fails open.

## Supported harnesses

The guard also runs in Codex, Devin for Terminal, Antigravity `agy`, and opencode. Install the hook files for every harness at once, from the project root:

```bash
ml-referee install --harness all
```

Pass a single harness name, such as `--harness codex`, to install only that one. The installer writes hook files and an `AGENTS.md` block, and it never overwrites your existing hooks. Codex needs you to trust the hooks in `/hooks`. Only `agy` has been checked live. See `docs/harnesses.md` for the capability matrix, fail modes, and verification status.

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
