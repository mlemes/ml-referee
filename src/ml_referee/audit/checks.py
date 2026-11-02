"""Leakage audit checks."""

import ast
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import rankdata  # type: ignore[import-untyped]


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


def check_target_leak(
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
                if isinstance(node, ast.Assign) and node.lineno <= split.lineno
            ),
            key=lambda node: node.lineno,
            reverse=True,
        )
        for assignment in assignments:
            targets = {
                node.id
                for target in assignment.targets
                for node in ast.walk(target)
                if isinstance(node, ast.Name)
            }
            if inputs & targets:
                inputs.update(
                    node.id
                    for node in ast.walk(assignment.value)
                    if isinstance(node, ast.Name)
                )
        for fit in calls:
            if (
                isinstance(fit.func, ast.Attribute)
                and fit.func.attr in {"fit", "fit_transform"}
                and fit.lineno < split.lineno
                and any(
                    isinstance(arg, ast.Name) and arg.id in inputs for arg in fit.args
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
