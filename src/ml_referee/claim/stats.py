"""Multiple-comparison statistics for claims."""


def bonferroni(p: float, n: int) -> float:
    """Return the Bonferroni-adjusted p-value, capped at 1."""
    raise NotImplementedError


def bh_adjust(ps: list[float]) -> list[float]:
    """Return Benjamini-Hochberg adjusted p-values in input order."""
    raise NotImplementedError


def selection_gap(
    validation_scores: list[float],
    holdout: float,
    higher_is_better: bool = True,  # noqa: FBT001, FBT002
) -> float:
    """Return the optimism of the best validation score over the holdout."""
    raise NotImplementedError
