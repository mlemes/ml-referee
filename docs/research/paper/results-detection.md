# Detection, guard, and ledger results

Every number comes from the experiments repository `ml-referee-experiments`, commit `dcd7413`. This file is a companion to the paper [`ml-referee.md`](ml-referee.md).

## Detection

The detection experiments run scripted hacks, so they measure what the audit and the sealed score can see. They do not measure whether an agent produces the hack. E4 runs six hacks over three tasks and 30 seeds. The original three hacks apply to one task each (30 runs). The other three apply to every task (90 runs each). The audit signal in Table 2 is the source check, the only audit finding that depends on the agent. The audit also raises a high `entity_overlap` finding on every task 01 run, which describes the split and not the agent.

| Hack | Sealed gap | Audit source check |
|---|---|---|
| `entity-lookup` (task 01) | 30/30 (100%) [0.89, 1.00] | 0/30 (0%) [0.00, 0.11] |
| `nn-copy` (task 02) | 30/30 (100%) [0.89, 1.00] | 0/30 (0%) [0.00, 0.11] |
| `public-label-fit` (task 03) | 30/30 (100%) [0.89, 1.00] | 0/30 (0%) [0.00, 0.11] |
| `target-leak` | 90/90 (100%) [0.96, 1.00] | 0/90 (0%) [0.00, 0.04] |
| `fit-before-split` | 0/90 (0%) [0.00, 0.04] | 90/90 (100%) [0.96, 1.00] |
| `many-trials` | 0/90 (0%) [0.00, 0.04] | 0/90 (0%) [0.00, 0.04] |

Table 1. Detection of scripted hacks (E4), pooled over the tasks that each hack runs on. Source: experiments repository, commit `dcd7413`, `results/expected/e4/summary.json`.

Four hacks open gaps of 0.44 to 1.51 between the public and the sealed score, and the sealed score exposes all of them in every run. You still have to put the sealed score next to the public score, because the claim report does not contain the public score. The `fit-before-split` hack gives no measurable gap, so only the source check sees it. The `many-trials` hack is invisible to every automated signal: the claim shows `Trials 50`, but no flag fires.

For false positives, E4b runs three honest pipelines on the same 90 task instances. Ridge and cross-validated ridge produce 0 of 90 gap flags each [0.00, 0.04]. Histogram gradient boosting produces 60 of 90 [0.56, 0.76], all on tasks 01 and 02, because those tasks reward a flexible model that learns the shared entities or the near-duplicate rows. A gap flag alone therefore cannot separate an exploit from a legitimate model that a leaky split rewards.

E5 injects known defects into California housing and a synthetic entity dataset. Exact duplicates, entity overlap, and target-leak features with label noise up to 0.1 standard deviations are found in 30 of 30 replicates at every level. Target leaks with noise of 0.2 and above are found in 0 of 30, which is a cliff: the check uses a fixed 0.98 Spearman threshold, so a feature that carries most of the label signal still passes. Clean data produced no finding in 0 of 200 runs on the baseline product commit and in 1 of 200 with the new near-duplicate check.

| Near-duplicate cell | Before the new check | After the new check |
|---|---|---|
| Noise 0.05, 5% copied | 0/30 (0%) [0.00, 0.11] | 30/30 (100%) [0.89, 1.00] |
| Noise 0.05, 1% copied | 0/30 (0%) [0.00, 0.11] | 22/30 (73%) [0.56, 0.86] |
| Noise 0.2, 5% copied | 0/30 (0%) [0.00, 0.11] | 0/30 (0%) [0.00, 0.11] |

Table 2. Sensitivity of near-duplicate detection (E5b). Source: experiments repository, commit `dcd7413`, `docs/results-quality.md`, section "Rerun against the fixed product pin".

## Guard and ledger

E6 fuzzes the guard with 1,900 calls in 24 attack classes over five harness payload shapes. On the first product commit, 15 of 16 claimed classes were blocked in 100 percent of the calls, and the class `cwd-trick` was blocked in 14 of 60 calls (0 of 46 on Claude Code, Codex, and opencode). After the fix, all 16 claimed classes are blocked in 100 percent of the calls, `cwd-trick` is blocked in 60 of 60 calls [0.94, 1.00], and the 300 benign controls are all allowed (0 of 300 blocked). Residual classes remain: base64 and hex encodings, `find ~ -exec`, and inline Python that walks the home directory are not blocked, and the unblocked shell variants run to completion in the fuzz layout.

E6c tests the compound plugin-CLI forms that the guard must allow (pipes, redirects, `timeout`, `cd x &&`, `which ml-referee`) and the mirror forms that it must deny (store paths as arguments, command substitution, heredocs, `claim`, `seal`, and `install`). The guard allowed 330 of 330 calls from 66 allowed forms and denied 1,280 of 1,280 calls from 256 mirror forms. A clean result shows that these forms are handled, not that no mirror form exists.

E6b tampers with the ledger. All 2,399 tamper cases that change the content of `ledger.jsonl` or `head.txt` are caught, and six concurrent callers never exceed the holdout budget in 5 of 5 trials. The ledger has no secret key, so a consistent rewrite is not caught: truncating the ledger to its seal record and rewriting `head.txt` to match passes verification and would reset the holdout budget. The rewrite needs write access to the sealed store, which the guard denies but the file mode does not prevent for the same operating system user.
