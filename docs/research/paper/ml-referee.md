# ml-referee: sealed evidence for agent-driven machine learning experiments

**Status:** draft. The paper reports results from scripted attacks, scripted agents, and one real-agent deterrence experiment with a small model. The Codex arm of the deterrence experiment is still running and is marked as pending. All experimental numbers come from the experiments repository `ml-referee-experiments`, commit `dcd7413`.

Authors: the list is not final.

## Abstract

Coding agents now run machine learning experiments end to end. They write the training code, choose the metric, and report the result. Recent studies find that such agents often game the evidence: across 17 language models, spontaneous reward hacking reached 30.5% on open-ended research-pipeline tasks [1], and about 57% of runs exploited a planted shortcut on tabular tasks [2]. Telling the agent not to cheat did not remove the behavior [2]. This paper proposes `ml-referee`, a plugin for Claude Code that separates the agent that produces a result from the evidence that certifies it. The plugin seals the holdout data and the evaluator outside the agent's workspace, blocks tool calls that touch them, records every trial in a hash-chained ledger, audits the data for leakage, and issues a claim only after one sealed evaluation that accounts for the number of trials. It defines a threat model, an architecture, and an evaluation protocol based on scripted attacks and on the benchmark of Prasad et al. [2].

The experiments give three results. First, the sealed evaluation exposes every scripted hack that opens a gap between the public and the sealed score (four of six hacks, in 30 of 30 or 90 of 90 runs each), and the audit adds a signal for one more (`fit-before-split`, 90 of 90). The `many-trials` hack goes undetected. Second, the guard blocks all 16 claimed attack classes after the experiments found and the product fixed one bypass. Third, in a real-agent test with Claude Haiku on three synthetic tasks, the plugin deters only when the prompt tells the agent to use it. The hack rate is 18 of 30 without the plugin, 18 of 30 with the passive plugin, and 8 of 30 with the plugin plus a prompt that asks for an audit and a sealed evaluation (Fisher exact p = 0.018 uncorrected, 0.055 with a Bonferroni correction over three tests). The result comes from one small model and three synthetic tasks, and the no-plugin arm did not run concurrently. The plugin detects and certifies; it does not deter an agent that is not told to use it.

## Introduction

An autonomous research agent controls both the experiment and the evidence offered for it [1]. This breaks the usual separation between the person who tunes a model and the person who judges it. Three older problems become urgent when an agent runs the loop.

1. **Leakage.** Information from the evaluation data reaches training, and the reported score is inflated. Kapoor and Narayanan survey this failure across machine learning based science [3].
2. **Adaptive reuse of a holdout.** Each look at a holdout score that informs the next experiment weakens the holdout as an unbiased estimate [4, 5, 6]. Loops such as autoresearch [9] repeat this step many times in one night.
3. **Selection over many trials.** The best of many tries is biased upward. Multiple-testing corrections address this in statistics and finance [7, 8].

Existing Claude Code skills and servers log and visualize runs, or automate the loop, but none enforces who controls the evidence (Section "Related work"). This paper makes three contributions:

- A threat model for an agent that optimizes a score it can partly observe (Section "Threat model").
- A sealed-evidence architecture, implemented as a Claude Code plugin (Section "Design").
- An evaluation protocol with scripted attacks and a published benchmark (Section "Evaluation protocol").
- Experimental results for detection, enforcement, and deterrence, and the product defects the experiments found (Sections "Results" and "Product defects found by the experiments").

## Related work

**Agent reward hacking.** Huang et al. report spontaneous hacking on 30.5% of open-ended research-pipeline tasks, confirm 505 of 677 permitted attempts (74.6%) as exploits, and find that an LLM review panel misses 6.5% of them [1]. They call for metrics the agent cannot control and for independent recomputation. Prasad et al. release BAITBENCH, three tabular tasks with an optional shortcut that raises the public score but fails on a hidden test set [2]. Both works measure the problem. Neither ships a defense for practitioners.

**Leakage and holdout validity.** Kapoor and Narayanan document leakage in published results [3]. Dwork et al. show how to preserve validity under adaptive reuse of a holdout [4]. Blum and Hardt give the Ladder, a leaderboard mechanism that limits overfitting to a test set [5]. Recht et al. show that new test sets expose accuracy drops for models tuned on a long-lived benchmark [6]. The design uses these results to set a holdout budget and to justify one sealed evaluation per claim.

**Multiple comparisons.** Benjamini and Hochberg control the false discovery rate [8]. Bailey and López de Prado deflate a performance statistic by the number of trials [7]. The design feeds the trial count from the ledger into such adjustments.

**Tooling.** MLflow, Weights & Biases, and TensorBoard skills track experiments, and Hugging Face skills run training jobs. Autoresearch skills automate the propose-run-keep loop [9]. These tools record or accelerate the loop. They do not remove the agent's control over the evaluator or the holdout. The repository file `docs/research/related-work/landscape.md` lists them.

## Threat model

The adversary is the coding agent itself. It is not assumed to be malicious. It optimizes the reported score and can edit files, run shell commands, and read files as the same operating system user as the person. The assets are the holdout labels, the evaluator code, the trial ledger, and the final claim. The model has eight attack classes: evaluator tampering, holdout reads, train-test leakage, label distillation into hard-coded rules, holdout reuse, ledger tampering, unlogged trials, and metric substitution. A malicious person and any compromise of the operating system are out of scope. The full table is in `docs/research/threat-model/threat-model.md`.

## Design

`ml-referee` has five parts.

1. **Seal.** `/ml-seal` moves the source data into a store outside the workspace and writes the holdout, the evaluator, and a manifest of hashes. It rewrites the workspace train file with the non-holdout rows. There is no validation split.
2. **Guard.** A `PreToolUse` hook denies edits to `.ml-referee.toml` and any tool call that touches the store. It fails closed. It applies normalized-substring rules and symlink-aware path resolution.
3. **Ledger.** A `PostToolUse` hook appends each command that matches a configured trial pattern to an append-only, hash-chained log. It matches per shell segment after it strips environment assignments and wrappers such as `nohup`, `env`, `time`, `nice`, and `sh -c`. The log yields the trial count `N`.
4. **Audit.** `/ml-audit` runs deterministic leakage checks: duplicate rows, entity overlap, target-leaking features, and preprocessing fit before the split. A reviewer subagent on a different model then reads the diff.
5. **Claim.** `/ml-claim` verifies the store, the ledger, and the manifest, runs the sealed evaluation within a holdout budget (default 1), adjusts the p-value you supply for `N`, reports the selection gap, and writes a report. The runner holds a lock, kills the evaluator process group on timeout, and never includes evaluator output in errors.

Hooks are a policy layer, not a sandbox. An agent with unrestricted shell access can route around them. Section "Limitations" states the residual risk.

## Evaluation protocol

The evaluation asks four questions.

- **RQ1, enforcement.** What fraction of scripted attacks does the guard block?
- **RQ2, detection.** What fraction of BAITBENCH shortcuts [2] does the audit flag?
- **RQ3, calibration.** How large is the gap between the reported score and the sealed-holdout score, with and without the plugin?
- **RQ4, cost.** What is the median hook latency, and how much does a run slow down?

Baselines are no plugin, a prompt that forbids cheating, and permission deny rules alone. The full protocol, including metrics and threats to validity, is in `docs/research/evaluation/protocol.md`.

The experiments repository implements part of this protocol. Table 1 lists the experiments that this paper reports.

| ID | What it tests | Agent | Runs |
|---|---|---|---|
| E1 | Scripted attacks A1 to A8 as synthetic tool-call events | None | 8 attacks, 3 conditions |
| E2 | BAITBENCH tasks with scripted `honest` and `hack` agents | Scripted | 36 |
| E3 | One BAITBENCH task with real agents | Claude Code, Codex | 1 per cell |
| E4, E4b | Detection of six scripted hacks, and false positives on three honest pipelines | Scripted | 630 and 270 |
| E5, E5b | Injected leakage defects and a sensitivity curve for near duplicates | Scripted | 30 per cell |
| E6, E6c | Guard fuzz and compound plugin-CLI forms | None (hook payloads) | 1,900 calls and 1,610 calls |
| E6b | Ledger tampering and concurrent budget use | None | 2,400 tamper cases |
| E7 | Deterrence: does the plugin change how often a real agent overfits the public split | Claude Code (Haiku) | 90, then 60 |

Table 1. Experiments reported in this paper. The conditions are C0 (no plugin), C1 (prompt that forbids cheating, not run), C2 (permission deny rules only, E1 only), C3 (passive plugin, the agent is not told to use it), and C4 (plugin plus a prompt that asks for an audit and one sealed evaluation). The three tasks are `regression_task_01` (shared entities), `regression_task_02` (near-duplicate rows), and `classification_task_03` (public labels), size n10k. The experiments do not measure RQ4 (cost).

A run is a hack when its hidden score is worse than its public score by more than 25 percent of the honest hidden score for the same task and seed. The honest score is the hidden score of a scripted ridge or logistic regression. All proportions below are counts with a Wilson 95 percent interval in square brackets.

## Results

Every number in this section comes from the experiments repository, commit `dcd7413`. The detection tables (E4, E4b, E5) come from `docs/results-quality.md` and `results/expected/e4` to `results/expected/e5`. The guard and ledger results (E6, E6b, E6c) come from the same page and from `results/expected/e6`, `results/expected/e6b`, and `results/expected/cli`. The deterrence results (E7) come from `docs/results-deterrence.md` and `results/expected/deterrence` and `results/expected/deterrence-cli`.

### Detection

E4 runs six scripted hacks over three tasks and 30 seeds. The audit column is the source check, the only audit finding that depends on the agent. The audit also raises a high `entity_overlap` finding on every task 01 run, which describes the split and not the agent.

| Hack | Sealed gap | Audit source check |
|---|---|---|
| `entity-lookup` (task 01) | 30/30 (100%) [0.89, 1.00] | 0/30 (0%) [0.00, 0.11] |
| `nn-copy` (task 02) | 30/30 (100%) [0.89, 1.00] | 0/30 (0%) [0.00, 0.11] |
| `public-label-fit` (task 03) | 30/30 (100%) [0.89, 1.00] | 0/30 (0%) [0.00, 0.11] |
| `target-leak` | 90/90 (100%) [0.96, 1.00] | 0/90 (0%) [0.00, 0.04] |
| `fit-before-split` | 0/90 (0%) [0.00, 0.04] | 90/90 (100%) [0.96, 1.00] |
| `many-trials` | 0/90 (0%) [0.00, 0.04] | 0/90 (0%) [0.00, 0.04] |

Table 2. Detection of scripted hacks (E4). Source: experiments repository, commit `dcd7413`, `results/expected/e4/summary.json`.

Four hacks open gaps of 0.44 to 1.51 and the sealed score exposes them in every run, but you must compare it with the public score because the claim report does not contain that score. Only the source check sees `fit-before-split`. Nothing flags `many-trials`. In E4b, ridge and cross-validated ridge produce 0 of 90 gap flags each [0.00, 0.04], and gradient boosting produces 60 of 90 [0.56, 0.76] on tasks 01 and 02, because those tasks reward a flexible model that learns the leak. In E5, exact duplicates, entity overlap, and target leaks with noise up to 0.1 standard deviations are found in 30 of 30 replicates, and target leaks with noise of 0.2 and above in 0 of 30. The new near-duplicate check raises detection at noise 0.05 with 5% copied from 0 of 30 to 30 of 30 [0.89, 1.00], and noise 0.2 stays undetected.

### Guard and ledger

E6 fuzzes the guard with 1,900 calls in 24 attack classes over five harness payload shapes. After the fix described below, all 16 claimed classes are blocked in 100 percent of the calls, and the 300 benign controls are all allowed. Residual classes (encodings, variable splitting, `find ~ -exec`, inline Python that walks the home directory) are not blocked. E6c finds that the guard allows 330 of 330 calls from 66 compound plugin-CLI forms and denies 1,280 of 1,280 calls from 256 mirror forms. E6b catches all 2,399 tamper cases that change the ledger content, and concurrent callers never exceed the holdout budget in 5 of 5 trials. A consistent rewrite of the ledger and `head.txt` is not caught, because the chain has no secret key.

### Real-agent deterrence

E7 uses Claude Code with Haiku on three tasks, seeds 1 to 10, and 30 runs per condition. The analysis is pre-registered: Wilson intervals and two-sided Fisher exact tests on pooled counts.

**First run (C4 invalid).** Hack rates were 18 of 30 (C0), 19 of 30 (C3), and 18 of 30 (C4), with p = 1.0 for both comparisons. The run does not test deterrence, because the guard rejected all 26 audit attempts and the one eval attempt in C4, so no agent saw an audit finding or a sealed score. The C0 results are valid and the second run reuses them.

**Second run.** The second run repeats C3 and C4 on the fixed product, with a holdout budget of 2 in both. Only C4 is told to use the tool.

| Condition | Hacks | Runs | Rate | 95% interval |
|---|---|---|---|---|
| C0 (no plugin, first run) | 18 | 30 | 60% | [0.42, 0.75] |
| C3 (passive plugin, tool available) | 18 | 30 | 60% | [0.42, 0.75] |
| C4 (plugin plus audit and eval prompt) | 8 | 30 | 27% | [0.14, 0.44] |

Table 3. Hack rate in the second E7 run. Source: experiments repository, commit `dcd7413`, `results/expected/deterrence-cli/summary.json`.

Fisher exact p is 1.0 for C0 against C3, and 0.018 for both C3 against C4 and C0 against C4. A Bonferroni correction over the three tests gives 0.055 for each C4 comparison. Per task, `regression_task_01` falls from 10 of 10 (C0) to 1 of 10 (C4), `regression_task_02` from 8 of 10 to 7 of 10, and `classification_task_03` stays at 0 of 10. The effect comes from one task, and the pooled test is not a stratified test.

All 30 C4 agents ran the audit and a sealed evaluation (compliance 30 of 30 [0.89, 1.00]), and 13 of 30 [0.27, 0.61] edited `solution.py` after the first output. Only 3 of 30 C3 agents ran the audit unprompted. A budget flaw affected C4: 12 of 30 agents ran the sealed evaluation twice, which used the whole budget of 2, so the harness claim failed in those runs. The hack flag does not depend on the claim.

#### Codex arm

**Pending.** The Codex (Luna) arm is still running. Its results will be reported here from `docs/results-deterrence-codex.md` on the experiments branch `e7-luna`. No Codex deterrence number appears in this paper.

## Product defects found by the experiments

The experiments found five defects in the plugin. The author of the plugin also wrote the tests, so this list shows what these tests found and not all defects.

1. **Working-directory bypass in the guard (E6).** The guard looked for `.ml-referee.toml` upward from the working directory in the hook payload. When a call reported a working directory outside the workspace tree, the guard found no config and allowed the call. Claude Code, Codex, and opencode allowed 46 of 46 such calls. The fix is in pull request #6, and the guard now blocks 60 of 60.
2. **Recorder patterns (E3).** The ledger recorded a trial only when a command segment matched a configured pattern by its literal prefix. A Codex run used `/tmp/mlr-xp/venv/bin/python3 evaluate.py`, which did not match `python evaluate.py*`, so the claim reported `Trials 0`. The recorder now normalizes absolute interpreter paths and environment assignments (pull request #6). In E7, 60 of 60 plugin runs in the second run have a ledger trial.
3. **Guard blocking the plugin CLI (E7, first run).** The guard treated any command that mentions `ml-referee` as a touch of the sealed store, except a single bare CLI call. It rejected pipes, redirects, `timeout`, `cd ... &&`, and `which ml-referee`, so no C4 agent got feedback. The fix allows the plugin CLI in compound commands. It is merged in pull request #8.
4. **Evaluation without the sealed rows (E7).** An agent never sees the sealed rows, so it cannot write the predictions that `ml-referee eval` needs. The first run worked around this with an unlabeled features file. The fix adds `ml-referee eval --predict-cmd`, which runs the agent's predictor on the sealed rows without labels. On the fixed commit all 30 C4 agents got a score from it, and it never returned a label (same pull request as item 3).
5. **Near-duplicate gap (E5).** The audit had no check for near-duplicate rows, and detection was 0 of 270 injected replicates. The product added `check_near_duplicates` (pull request #5), and detection at noise 0.05 with 5% copied rose from 0 of 30 to 30 of 30.

Two defects remain open. The guard rejects benign work: 51 of 57 rejections in the second E7 run were writes whose text contains `ml-referee` or shell commands with a standalone `*`. And a budget of 2 is too small when the agent may evaluate twice.

## Discussion

The results support a narrow claim. The plugin detects and certifies, and it deters only when the agent is told to use it.

**It detects.** The sealed score exposes every scripted hack that opens a gap, and the claim report exposed the gap in every hacked plugin run that has a claim, in E2, E3, and E7. The audit adds one agent-attributable signal, the source check for `fit-before-split`. An agent that hacks still produces the hack, and the plugin reports it afterward.

**It certifies.** The claim runs one sealed evaluation that accounts for the number of trials, and the ledger rejects every tamper that changes its content. A claim therefore carries a score that the agent did not control, which the public score does not.

**It does not deter a passive agent.** With the tool available but no instruction to use it, the hack rate is 60 percent, the same as with no plugin. Only 3 of 30 C3 agents ran the audit. The `AGENTS.md` block is neutral on purpose, and the agents did not pick up the tool by themselves.

**It deters when the agent is told to use it, in one task.** With the prompt, the hack rate falls from 60 to 27 percent. The reduction is concentrated in the task where the audit names a fixable cause. Where the signal is a large gap without a cause, agents mostly kept their solution. The evidence is one small model and three synthetic tasks, and it does not show deterrence in general.

**Design implication.** A user who wants deterrence has to make the agent run the audit and the sealed evaluation, for example in the task prompt or in a harness step. Installing the plugin alone gives detection and certification.

## Limitations

Same-user file permissions do not stop a determined agent. A stronger version needs a separate user, a container, or an evaluator in a separate process with no filesystem path to the holdout.

**Scope of the evidence.**

- The deterrence result comes from one small model (Claude Haiku), three synthetic BAITBENCH tasks, and 10 runs per task and condition (30 per condition). The intervals are wide: C4 is [0.14, 0.44] and C0 is [0.42, 0.75].
- C0 is not concurrent. It ran in the first run, and C3 and C4 ran later, so a change in the model or the host between runs would look like an effect.
- The C4 effect is borderline after correction: p = 0.018 uncorrected and 0.055 after a Bonferroni correction over three tests. The pooled test includes the 10 task 03 runs per condition where no agent hacks, and the effect is in one task.
- The Codex arm is pending, so no second model supports the deterrence result.
- The prompt wording matters. The C4 addendum is one wording that names the exact commands.
- The hack criterion uses the hidden split that is also the sealed holdout, and 12 C4 agents evaluated twice on those rows.
- The edit flags read tool calls from the transcript, so they miss a rewrite through an unusual shell construct.
- The same author wrote the plugin, the audit, the attacks, the scripted agents, and the tests.

**Detection gaps.**

- The `many-trials` hack is undetected by every automated signal. The selection gap is a weak signal: only 20 percent of the K=100 replicates exceed the 95th percentile of the calibration gaps.
- The target-leak check has a cliff. Noise of 0.1 label standard deviations is found in 30 of 30 replicates, and noise of 0.2 in 0 of 30.
- Near duplicates with noise of 0.2 standard deviations stay undetected by construction.
- The audit raises a high `entity_overlap` finding on every task 01 run, honest or not, and gradient boosting trips the gap flag on tasks 01 and 02. Neither finding separates an exploit from a legitimate model.
- The ledger has no key. A consistent rewrite of `ledger.jsonl` and `head.txt` passes verification. Version 2 needs a separate user or a container.
- The ledger cannot see trials that run outside the configured command patterns. Scripted attack A7 reports 0 percent blocked.

**Guard.**

- The guard is a policy layer, not a sandbox. The E6 residual classes (encodings, variable splitting, `find ~ -exec`, inline Python that walks the home directory) are not blocked, and the unblocked variants run to completion in the fuzz layout.
- The guard accepts the known residuals: `find ~ -name 'holdout.parquet' -exec cat`, glob-obfuscated `cd` chains (E6 blocked 73 of 73 variants, which does not show that the residual is closed), and inline Python that walks the home directory.
- The agent can run the `ml-referee` CLI itself, so it gets at most `holdout_budget` sealed looks.
- If `python3` is missing, a hook crash does not block in Claude Code, so the guard fails open.
- The E6 and E6c cases come from the author's list of attacks, and the hook runs in process on the host, not inside the agent container.
- Live checks cover only the agy harness. Codex, Devin, and opencode are supported from their vendor documentation and have not been run live for the guard.

**Other.**

- The cited 2026 studies are arXiv preprints and have not been peer reviewed [1, 2].

## Conclusion

`ml-referee` separates the agent that produces a result from the evidence that certifies it. In scripted tests, the sealed evaluation exposes every hack that opens a gap, the guard blocks all claimed attack classes after the experiments exposed one bypass, and the ledger catches every tamper that changes its content. The experiments also show what the plugin does not do: it misses the `many-trials` hack, it has a target-leak cliff, and it is a policy layer and not a sandbox. In the one real-agent test, a passive plugin did not change the hack rate (18 of 30 against 18 of 30), and a plugin plus a prompt that asks for the audit and a sealed evaluation lowered it to 8 of 30. That result needs replication with more models, more tasks, and a concurrent control before you rely on it. Until then, treat the plugin as a tool that detects and certifies, and that deters when the agent is told to use it.

## Companion documents

- `docs/research/paper/results-detection.md`: detection, guard, and ledger results in full.
- `docs/research/paper/results-deterrence.md`: the deterrence experiment in full, including the Codex arm when it finishes.
- `docs/research/evaluation/protocol.md`: the evaluation protocol.
- `docs/research/threat-model/threat-model.md`: the threat model.
- Experiments repository `ml-referee-experiments`, commit `dcd7413`: `docs/protocol.md`, `docs/results.md`, `docs/results-quality.md`, `docs/results-deterrence.md`, and `results/expected/`.

## References

[1] Y. Huang, Z. Xu, Y. Ma, W. Wang, Z. Liu, Z. Xu, P.-Y. Chen, M. Galley, Z. Lin, S. Feuerriegel, R. Poovendran, M. Sra, A. Pentland, X. Zhang, and Z. Chen. "Reward Hacking Challenges Oversight of Autonomous Research Agents." arXiv:2609.28614, 2026. https://arxiv.org/abs/2609.28614

[2] P. S. Prasad, M. Anto, L. Eshuijs, J. Moncarz, K. Kislay, and J. J. Vazquez. "BAITBENCH: Measuring Agent Reward Hacking with Optional Shortcuts Planted in ML Tasks." arXiv:2608.30724, 2026. https://arxiv.org/abs/2608.30724

[3] S. Kapoor and A. Narayanan. "Leakage and the Reproducibility Crisis in ML-based Science." arXiv:2207.07048, 2022. https://arxiv.org/abs/2207.07048

[4] C. Dwork, V. Feldman, M. Hardt, T. Pitassi, O. Reingold, and A. Roth. "Preserving Statistical Validity in Adaptive Data Analysis." arXiv:1411.2664, 2014 (v3, 2016). https://arxiv.org/abs/1411.2664

[5] A. Blum and M. Hardt. "The Ladder: A Reliable Leaderboard for Machine Learning Competitions." arXiv:1502.04585, 2015. https://arxiv.org/abs/1502.04585

[6] B. Recht, R. Roelofs, L. Schmidt, and V. Shankar. "Do ImageNet Classifiers Generalize to ImageNet?" arXiv:1902.10811, 2019. https://arxiv.org/abs/1902.10811

[7] D. H. Bailey and M. López de Prado. "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality." *Journal of Portfolio Management* 40(5):94-107, 2014. https://doi.org/10.3905/jpm.2014.40.5.094

[8] Y. Benjamini and Y. Hochberg. "Controlling the False Discovery Rate: A Practical and Powerful Approach to Multiple Testing." *Journal of the Royal Statistical Society: Series B* 57(1):289-300, 1995. https://doi.org/10.1111/j.2517-6161.1995.tb02031.x

[9] A. Karpathy. "autoresearch." GitHub repository, 2026. https://github.com/karpathy/autoresearch (accessed 2026-11-02).
