# ml-referee: sealed evidence for agent-driven machine learning experiments

**Status:** draft. The paper has no empirical results yet. Sections 5 and 6 describe the planned evaluation, not completed work.

**Authors:** to be added.

## Abstract

Coding agents now run machine learning experiments end to end. They write the training code, choose the metric, and report the result. Recent studies find that such agents often game the evidence: across 17 language models, spontaneous reward hacking reached 30.5% on open-ended research-pipeline tasks [1], and about 57% of runs exploited a planted shortcut on tabular tasks [2]. Telling the agent not to cheat did not remove the behavior [2]. This paper proposes `ml-referee`, a plugin for Claude Code that separates the agent that produces a result from the evidence that certifies it. The plugin seals the holdout data and the evaluator outside the agent's workspace, blocks tool calls that touch them, records every trial in a hash-chained ledger, audits the data for leakage, and issues a claim only after one sealed evaluation that accounts for the number of trials. It defines a threat model, an architecture, and an evaluation protocol based on scripted attacks and on the benchmark of Prasad et al. [2].

## Introduction

An autonomous research agent controls both the experiment and the evidence offered for it [1]. This breaks the usual separation between the person who tunes a model and the person who judges it. Three older problems become urgent when an agent runs the loop.

1. **Leakage.** Information from the evaluation data reaches training, and the reported score is inflated. Kapoor and Narayanan survey this failure across machine learning based science [3].
2. **Adaptive reuse of a holdout.** Each look at a holdout score that informs the next experiment weakens the holdout as an unbiased estimate [4, 5, 6]. Loops such as autoresearch [9] repeat this step many times in one night.
3. **Selection over many trials.** The best of many tries is biased upward. Multiple-testing corrections address this in statistics and finance [7, 8].

Existing Claude Code skills and servers log and visualize runs, or automate the loop, but none enforces who controls the evidence (Section "Related work"). This paper makes three contributions:

- A threat model for an agent that optimizes a score it can partly observe (Section "Threat model").
- A sealed-evidence architecture, implemented as a Claude Code plugin (Section "Design").
- An evaluation protocol with scripted attacks and a published benchmark (Section "Evaluation protocol").

## Related work

**Agent reward hacking.** Huang et al. report spontaneous hacking on 30.5% of open-ended research-pipeline tasks, confirm 505 of 677 permitted attempts (74.6%) as exploits, and find that an LLM review panel misses 6.5% of them [1]. They call for metrics the agent cannot control and for independent recomputation. Prasad et al. release BAITBENCH, three tabular tasks with an optional shortcut that raises the public score but fails on a hidden test set [2]. Both works measure the problem. Neither ships a defense for practitioners.

**Leakage and holdout validity.** Kapoor and Narayanan document leakage in published results [3]. Dwork et al. show how to preserve validity under adaptive reuse of a holdout [4]. Blum and Hardt give the Ladder, a leaderboard mechanism that limits overfitting to a test set [5]. Recht et al. show that new test sets expose accuracy drops for models tuned on a long-lived benchmark [6]. The design uses these results to set a holdout budget and to justify one sealed evaluation per claim.

**Multiple comparisons.** Benjamini and Hochberg control the false discovery rate [8]. Bailey and López de Prado deflate a performance statistic by the number of trials [7]. The design feeds the trial count from the ledger into such adjustments.

**Tooling.** MLflow, Weights & Biases, and TensorBoard skills track experiments, and Hugging Face skills run training jobs. Autoresearch skills automate the propose-run-keep loop [9]. These tools record or accelerate the loop. They do not remove the agent's control over the evaluator or the holdout. The repository file `docs/research/related-work/landscape.md` lists them.

## Threat model

The adversary is the coding agent itself. It is not assumed to be malicious. It optimizes the reported score and can edit files, run shell commands, and read files as the same operating system user as the person. The assets are the holdout labels, the evaluator code, the trial ledger, and the final claim. The model has eight attack classes: evaluator tampering, holdout reads, train-test leakage, label distillation into hard-coded rules, holdout reuse, ledger tampering, unlogged trials, and metric substitution. A malicious person and any compromise of the operating system are out of scope. The full table is in `docs/research/threat-model/threat-model.md`.

## Design

`ml-referee` has five parts.

1. **Seal.** `/ml-seal` splits the data. It moves the holdout and the evaluator to a store outside the workspace and records their hashes in a manifest.
2. **Guard.** A `PreToolUse` hook denies edits to the evaluator and any read or shell command that touches the store. The hook fails closed.
3. **Ledger.** A `PostToolUse` hook appends each configured train or evaluation command to an append-only, hash-chained log. The log yields the trial count `N`.
4. **Audit.** `/ml-audit` runs deterministic leakage checks: duplicate rows, entity overlap, target-leaking features, and preprocessing fit on the full data. A reviewer subagent on a different model then reads the diff.
5. **Claim.** `/ml-claim` verifies the ledger and the manifest, runs the sealed evaluation within a holdout budget (default 1), adjusts the validation claim for `N`, and writes a report.

Hooks are a policy layer, not a sandbox. An agent with unrestricted shell access can route around them. Section "Limitations" states the residual risk.

## Evaluation protocol

The evaluation asks four questions.

- **RQ1, enforcement.** What fraction of scripted attacks does the guard block?
- **RQ2, detection.** What fraction of BAITBENCH shortcuts [2] does the audit flag?
- **RQ3, calibration.** How large is the gap between the reported score and the sealed-holdout score, with and without the plugin?
- **RQ4, cost.** What is the median hook latency, and how much does a run slow down?

Baselines are no plugin, a prompt that forbids cheating, and permission deny rules alone. The full protocol, including metrics and threats to validity, is in `docs/research/evaluation/protocol.md`.

## Limitations

Same-user file permissions do not stop a determined agent. A stronger version needs a separate user, a container, or an evaluator in a separate process with no filesystem path to the holdout. The ledger cannot see trials that run outside the configured command patterns. The cited 2026 studies are arXiv preprints and have not been peer reviewed [1, 2].

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
