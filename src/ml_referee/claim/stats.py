"""Multiple-comparison statistics for claims."""


def bonferroni(p: float, n: int) -> float:
    """Return the Bonferroni-adjusted p-value, capped at 1."""
    if not 0 <= p <= 1 or n < 1:
        msg = "Require a p-value in [0, 1] and a positive trial count"
        raise ValueError(msg)
    return min(1.0, p * n)


def bh_adjust(ps: list[float]) -> list[float]:
    """Return Benjamini-Hochberg adjusted p-values in input order."""
    if any(not 0 <= p <= 1 for p in ps):
        msg = "Require p-values in [0, 1]"
        raise ValueError(msg)
    adjusted = [0.0] * len(ps)
    running = 1.0
    ordered = sorted(range(len(ps)), key=ps.__getitem__)
    for rank in range(len(ps), 0, -1):
        index = ordered[rank - 1]
        running = min(running, ps[index] * len(ps) / rank)
        adjusted[index] = running
    return adjusted


def selection_gap(
    validation_scores: list[float],
    holdout: float,
    higher_is_better: bool = True,  # noqa: FBT001, FBT002
) -> float:
    """Return the optimism of the best validation score over the holdout."""
    if not validation_scores:
        msg = "Require at least one validation score"
        raise ValueError(msg)
    if higher_is_better:
        return max(validation_scores) - holdout
    return holdout - min(validation_scores)
