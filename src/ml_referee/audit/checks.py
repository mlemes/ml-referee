"""Leakage audit checks."""

import ast
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree  # type: ignore[import-untyped]
from scipy.stats import rankdata  # type: ignore[import-untyped]

# Near-duplicate check: a holdout row is "near" a training row when its
# nearest-neighbour distance, in training-standardized numeric features, is at
# most the NEAR_DUP_QUANTILE quantile of the training-to-training
# nearest-neighbour distances. The quantile was chosen on development seeds that
# the experiments repository does not use.
NEAR_DUP_QUANTILE = 0.002
NEAR_DUP_MAX_ROWS = 20_000
NEAR_DUP_MEDIUM = 0.01
NEAR_DUP_HIGH = 0.05
NEAR_DUP_MIN_ROWS = 3


@dataclass(frozen=True)
class Finding:
    """One audit finding."""

    check: str
    severity: str
    detail: str


def check_duplicates(train: pd.DataFrame, holdout_keys: pd.DataFrame) -> list[Finding]:
    """Flag exact duplicate rows across splits."""
    columns = train.columns.intersection(holdout_keys.columns, sort=False)
    if columns.empty:
        return [Finding("duplicates", "info", "No shared columns to compare.")]
    # Normalize floating point noise before hashing shared columns.
    combined = pd.concat([train[columns], holdout_keys[columns]], ignore_index=True)
    hashes = pd.util.hash_pandas_object(combined.round(12), index=False)
    count = int(hashes.iloc[: len(train)].isin(hashes.iloc[len(train) :]).sum())
    return (
        [Finding("duplicates", "high", f"{count} training rows overlap holdout.")]
        if count
        else []
    )


def _near_dup_rows(count: int) -> tuple[np.ndarray, bool]:
    """Return the row positions to use and whether they are a subsample."""
    if count <= NEAR_DUP_MAX_ROWS:
        return np.arange(count), False
    rng = np.random.default_rng(0)
    chosen = rng.choice(count, size=NEAR_DUP_MAX_ROWS, replace=False)
    return np.sort(chosen), True


def check_near_duplicates(
    train: pd.DataFrame,
    holdout: pd.DataFrame,
    label: str,
    threshold: float | None = None,
    group: str | None = None,
) -> list[Finding]:
    """Flag holdout rows that nearly duplicate a training row.

    The label and the group column are ignored. Shared numeric columns are
    standardized with the training mean and standard deviation (missing values
    take the training median). The threshold is the ``NEAR_DUP_QUANTILE``
    quantile of the training-to-training nearest-neighbour distances unless
    you pass one in standardized units. Severity is high above
    ``NEAR_DUP_HIGH`` of the holdout rows and medium above ``NEAR_DUP_MEDIUM``
    (at least ``NEAR_DUP_MIN_ROWS`` rows). Each side is capped at
    ``NEAR_DUP_MAX_ROWS`` rows with a fixed-seed subsample. Output holds counts
    only.
    """
    columns = [
        column
        for column in train.columns.intersection(holdout.columns, sort=False)
        if column not in {label, group}
        and pd.api.types.is_numeric_dtype(train[column])
        and pd.api.types.is_numeric_dtype(holdout[column])
        and not pd.api.types.is_bool_dtype(train[column])
        and not pd.api.types.is_bool_dtype(holdout[column])
    ]
    if not columns:
        return [
            Finding("near_duplicates", "info", "No shared numeric feature columns."),
        ]
    if len(train) < 2 or len(holdout) < 1:  # noqa: PLR2004
        return [Finding("near_duplicates", "info", "Too few rows for this check.")]
    train_rows, train_sampled = _near_dup_rows(len(train))
    hold_rows, hold_sampled = _near_dup_rows(len(holdout))
    x_all = np.array(train[columns].iloc[train_rows], dtype=float)
    h_all = np.array(holdout[columns].iloc[hold_rows], dtype=float)
    x_all[~np.isfinite(x_all)] = np.nan
    h_all[~np.isfinite(h_all)] = np.nan
    keep = np.isfinite(x_all).any(axis=0)
    if not keep.any():
        return [
            Finding("near_duplicates", "info", "No shared numeric feature columns."),
        ]
    x_all, h_all = x_all[:, keep], h_all[:, keep]
    median = np.nanmedian(x_all, axis=0)
    x_all = np.where(np.isnan(x_all), median, x_all)
    h_all = np.where(np.isnan(h_all), median, h_all)
    mean, std = x_all.mean(axis=0), x_all.std(axis=0)
    std = np.where(std > 0, std, 1.0)
    x = (x_all - mean) / std
    h = (h_all - mean) / std
    tree = cKDTree(x)
    if threshold is None:
        within_train = tree.query(x, k=2, workers=-1)[0][:, 1]
        # Exact repeats inside training (distance 0) must not pull the threshold to 0.
        positive = within_train[within_train > 0]
        threshold = (
            float(np.quantile(positive, NEAR_DUP_QUANTILE)) if positive.size else 0.0
        )
    nearest = tree.query(h, k=1, workers=-1)[0]
    flagged = int((nearest <= threshold).sum())
    fraction = flagged / len(h)
    if flagged < NEAR_DUP_MIN_ROWS or fraction <= NEAR_DUP_MEDIUM:
        return []
    note = " (rows subsampled)" if train_sampled or hold_sampled else ""
    return [
        Finding(
            "near_duplicates",
            "high" if fraction > NEAR_DUP_HIGH else "medium",
            f"{flagged} of {len(h)} holdout rows ({fraction:.1%}) have a near "
            f"duplicate in training{note}.",
        ),
    ]


def check_entity_overlap(
    train: pd.DataFrame,
    holdout: pd.DataFrame,
    group: str | None,
) -> list[Finding]:
    """Flag group entities present in both splits."""
    if group is None or group not in train or group not in holdout:
        return [
            Finding("entity_overlap", "info", f"Group column {group!r} unavailable."),
        ]
    findings = []
    missing = int(train[group].isna().sum() + holdout[group].isna().sum())
    if missing:
        findings.append(
            Finding("entity_overlap", "medium", f"{missing} missing group values."),
        )
    overlap = train[group].dropna().drop_duplicates().isin(holdout[group].dropna())
    count = int(overlap.sum())
    if count:
        findings.append(
            Finding("entity_overlap", "high", f"{count} shared entities in {group!r}."),
        )
    return findings


def check_target_leak(  # noqa: C901
    train: pd.DataFrame,
    label: str,
    auc_threshold: float = 0.98,
) -> list[Finding]:
    """Flag features that nearly separate the label."""
    if label not in train:
        return [Finding("target_leak", "info", f"Label {label!r} unavailable.")]
    target = train[label]
    classes = target.dropna().unique()
    if len(classes) < 2:  # noqa: PLR2004
        return [Finding("target_leak", "info", "Label has fewer than two classes.")]
    findings = []
    for column in train.select_dtypes(include="number").columns.drop(
        label, errors="ignore",
    ):
        feature = train[column]
        valid = feature.notna() & target.notna()
        x, y = feature[valid].to_numpy(), target[valid].to_numpy()
        if not len(x):
            continue
        if len(classes) != 2:  # noqa: PLR2004
            if np.array_equal(x, y):
                findings.append(
                    Finding("target_leak", "high", f"{column!r} equals label."),
                )
            elif pd.api.types.is_numeric_dtype(target):
                x_ranks, y_ranks = rankdata(x), rankdata(y)
                if np.ptp(x_ranks) > 0 and np.ptp(y_ranks) > 0:
                    spearman = float(np.corrcoef(x_ranks, y_ranks)[0, 1])
                    if abs(spearman) >= auc_threshold:
                        findings.append(
                            Finding(
                                "target_leak", "high",
                                f"{column!r} Spearman={spearman:.6f}.",
                            ),
                        )
            continue
        positive = y == classes[1]
        n_positive = int(positive.sum())
        n_negative = len(y) - n_positive
        if not n_positive or not n_negative:
            continue
        ranks = rankdata(x)
        auc = float(
            (ranks[positive].sum() - n_positive * (n_positive + 1) / 2)
            / (n_positive * n_negative),
        )
        auc = max(auc, 1 - auc)
        if auc >= auc_threshold:
            findings.append(
                Finding("target_leak", "high", f"{column!r} AUC={auc:.6f}."),
            )
    return findings


def _scope_nodes(node: ast.AST) -> list[ast.AST]:
    """Collect one lexical scope, excluding nested scope bodies."""
    nodes = [node]
    for child in ast.iter_child_nodes(node):
        if not isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        ):
            nodes.extend(_scope_nodes(child))
    return nodes


def _data_names(node: ast.AST) -> set[str]:
    """Collect data dependencies, including receivers but excluding bare callees."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Subscript):
        return _data_names(node.value)
    children = (
        [
            *([node.func.value] if isinstance(node.func, ast.Attribute) else []),
            *node.args,
            *(keyword.value for keyword in node.keywords),
        ]
        if isinstance(node, ast.Call)
        else ast.iter_child_nodes(node)
    )
    return {name for child in children for name in _data_names(child)}


def _split_inputs(nodes: list[ast.AST], split: ast.Call) -> set[str]:
    """Resolve assignment dependencies in both directions before a split."""
    inputs = {
        node.id
        for arg in split.args
        for node in ast.walk(arg)
        if isinstance(node, ast.Name)
    }
    assignments = sorted(
        (
            node
            for node in nodes
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and node.lineno < split.lineno
        ),
        key=lambda node: node.lineno,
        reverse=True,
    )
    dependencies = []
    for assignment in assignments:
        targets = {
            node.id
            for target in (
                assignment.targets
                if isinstance(assignment, ast.Assign)
                else [assignment.target]
            )
            for node in ast.walk(target)
            if isinstance(node, ast.Name)
        }
        if assignment.value is not None:
            dependencies.append((targets, _data_names(assignment.value)))
    while True:
        previous = inputs.copy()
        for targets, sources in dependencies:
            if inputs & (targets | sources):
                inputs.update(targets | sources)
        if inputs == previous:
            break
    return inputs


def _fit_findings(scope: ast.AST, path: Path) -> list[Finding]:
    """Match earlier fits to split inputs and their assignment dependencies."""
    nodes = _scope_nodes(scope)
    calls = [node for node in nodes if isinstance(node, ast.Call)]
    findings = []
    for split in calls:
        name = (
            split.func.id
            if isinstance(split.func, ast.Name)
            else getattr(split.func, "attr", "")
        )
        if name != "train_test_split":
            continue
        inputs = _split_inputs(nodes, split)
        for fit in calls:
            if (
                isinstance(fit.func, ast.Attribute)
                and fit.func.attr in {"fit", "fit_transform"}
                and fit.lineno < split.lineno
                and any(
                    isinstance(node, ast.Name) and node.id in inputs
                    for arg in [*fit.args, *(kw.value for kw in fit.keywords)]
                    for node in ast.walk(arg)
                )
            ):
                finding = Finding(
                    "fit_on_full", "high", f"{path}:{fit.lineno}: fit before split.",
                )
                if finding not in findings:
                    findings.append(finding)
    return findings


def check_fit_on_full(sources: list[Path]) -> list[Finding]:
    """Flag fit/fit_transform on data before the split."""
    findings = []
    for path in sources:
        try:
            tree = ast.parse(path.read_text())
        except (OSError, SyntaxError, UnicodeError) as exc:
            findings.append(Finding("fit_on_full", "info", f"{path}: {exc}"))
            continue
        # shortcut: lexical tracing only; use dataflow analysis for dynamic aliases.
        scopes = [
            tree,
            *(
                node
                for node in ast.walk(tree)
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                )
            ),
        ]
        for scope in scopes:
            findings.extend(_fit_findings(scope, path))
    return findings
