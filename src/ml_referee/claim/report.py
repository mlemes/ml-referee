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
    higher_is_better: bool = True,  # noqa: FBT001, FBT002
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
            higher_is_better=manifest.higher_is_better and higher_is_better,
        )
        gap = f"Selection gap: {value:.12g}"
    audit = "\n".join(
        f"- {finding.severity}: {finding.check}: {finding.detail}"
        for finding in findings
    ) or "No audit findings supplied."
    return (
        "# Claim\n\nSealed holdout evaluation.\n\n"
        f"## Sealed score\n\n{result.metric}: {result.value} (n={result.n})\n\n"
        f"## Trials\n\n{trials}\n\n"
        f"## Adjusted result\n\n{adjusted}\n\n{gap}\n\n"
        f"## Audit findings\n\n{audit}\n\n"
        "## Limits\n\nHooks are a policy layer, not a sandbox.\n"
    ).replace("\u2014", "-")


def claim(
    cfg: Config,
    predictions: Path,
    validation_scores: list[float],
    p_value: float | None = None,
    findings: list[Finding] | None = None,
) -> str:
    """Verify the store, run one sealed eval, and return the report."""
    directory = store_dir(cfg)
    manifest = _verify_store(cfg, directory)
    trials = Ledger(directory / "ledger.jsonl").trial_count()
    if p_value is not None:
        bonferroni(p_value, max(1, trials))
    result = run_holdout_eval(cfg, predictions)
    return build_report(
        manifest, result, trials, findings or [], validation_scores, p_value,
    )
