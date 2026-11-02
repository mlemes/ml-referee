"""Claim report and sealed claim runner."""

from pathlib import Path

from ml_referee.audit import Finding
from ml_referee.claim.stats import bonferroni, selection_gap
from ml_referee.config import Config, store_dir
from ml_referee.ledger import Ledger
from ml_referee.runner import EvalResult, _verify_store, run_holdout_eval
from ml_referee.seal import Manifest


def build_report(  # noqa: PLR0913, PLR0917
    manifest: Manifest,
    result: EvalResult,
    trials: int,
    findings: list[Finding],
    validation_scores: list[float],
    p_value: float | None = None,
    higher_is_better: bool | None = None,  # noqa: FBT001
) -> str:
    """Build the Markdown claim report."""
    adjusted = (
        f"Bonferroni-adjusted p-value: {bonferroni(p_value, max(1, trials))}"
        if p_value is not None
        else "Adjusted p-value unavailable: no p-value supplied."
    )
    gap = "Selection gap unavailable: no validation scores supplied."
    if validation_scores:
        value = selection_gap(
            validation_scores, result.value,
            higher_is_better=(
                manifest.higher_is_better
                if higher_is_better is None else higher_is_better
            ),
        )
        gap = f"Selection gap: {value:.12g}"
    audit = "\n".join(
        f"- {finding.severity}: {finding.check}: {finding.detail}"
        for finding in findings
    ) or "No audit findings."
    return (
        "# Claim\n\nSealed holdout evaluation.\n\n"
        f"## Sealed score\n\n{result.metric}: {result.value} (n={result.n})\n\n"
        f"## Trials\n\n{trials}\n\n"
        f"## Adjusted result\n\n{adjusted}\n\n{gap}\n\n"
        f"## Audit findings\n\n{audit}\n\n"
        "## Limits\n\nHooks are a policy layer, not a sandbox.\n"
    ).replace("\u2014", "-")


def claim(  # noqa: PLR0913, PLR0917
    cfg: Config,
    predictions: Path,
    validation_scores: list[float] | None = None,
    p_value: float | None = None,
    findings: list[Finding] | None = None,
    higher_is_better: bool | None = None,  # noqa: FBT001
) -> str:
    """Verify the store, run one sealed eval, and return the report."""
    validation_scores = validation_scores if validation_scores is not None else []
    if validation_scores:
        selection_gap(validation_scores, 0.0)
    if p_value is not None:
        bonferroni(p_value, 1)
    directory = store_dir(cfg)
    manifest = _verify_store(cfg, directory)
    trials = Ledger(directory / "ledger.jsonl").trial_count()
    result = run_holdout_eval(cfg, predictions)
    return build_report(
        manifest, result, trials, findings or [], validation_scores, p_value,
        higher_is_better=higher_is_better,
    )
