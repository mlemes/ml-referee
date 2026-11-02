from pathlib import Path

import pytest

from ml_referee.audit import Finding
from ml_referee.claim import (
    bh_adjust,
    bonferroni,
    build_report,
    claim,
    selection_gap,
)
from ml_referee.config import store_dir
from ml_referee.ledger import Ledger
from ml_referee.runner import (
    BudgetExhausted,
    EvalResult,
    IntegrityError,
)
from ml_referee.seal import Manifest, load_manifest

from tests.test_runner import _setup


def _manifest(higher: bool = True) -> Manifest:
    return Manifest(
        holdout_sha256="a" * 64,
        evaluator_sha256="b" * 64,
        seed=1,
        metric="acc",
        higher_is_better=higher,
        n_holdout=25,
    )


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    nxt = text.find("\n#", start + len(heading))
    return text[start:] if nxt == -1 else text[start:nxt]


def test_bonferroni_caps_at_one() -> None:
    assert bonferroni(0.04, 50) == 1.0


def test_bonferroni_scales() -> None:
    assert bonferroni(0.01, 5) == pytest.approx(0.05)


@pytest.mark.parametrize(("p", "n"), [(-0.1, 3), (1.1, 3), (0.5, 0)])
def test_bonferroni_rejects_bad_input(p: float, n: int) -> None:
    with pytest.raises(ValueError):
        bonferroni(p, n)


def test_bh_matches_known_example() -> None:
    got = bh_adjust([0.01, 0.04, 0.03, 0.005])
    assert got == pytest.approx([0.02, 0.04, 0.04, 0.02])


def test_bh_empty_and_cap() -> None:
    assert bh_adjust([]) == []
    assert bh_adjust([0.9, 0.8]) == pytest.approx([0.9, 0.9])
    assert all(v <= 1.0 for v in bh_adjust([0.99, 0.98, 0.97]))


def test_selection_gap_positive_when_val_exceeds_holdout() -> None:
    assert selection_gap([0.8, 0.9, 0.85], 0.7) == pytest.approx(0.2)


def test_selection_gap_lower_is_better() -> None:
    gap = selection_gap([0.5, 0.3, 0.4], 0.45, higher_is_better=False)
    assert gap == pytest.approx(0.15)


def test_selection_gap_empty_raises() -> None:
    with pytest.raises(ValueError):
        selection_gap([], 0.5)


def test_report_contains_trial_count_and_limits_sentence() -> None:
    result = EvalResult(metric="acc", value=0.8123, n=25)
    text = build_report(_manifest(), result, 37, [], [0.9])
    for heading in (
        "Claim",
        "Sealed score",
        "Trials",
        "Adjusted result",
        "Audit findings",
        "Limits",
    ):
        assert heading in text
    assert "37" in _section(text, "Trials")
    assert "acc" in text
    assert "0.8123" in text
    assert "Hooks are a policy layer, not a sandbox." in _section(text, "Limits")
    assert "—" not in text


def test_report_adjusted_p_and_gap_and_findings() -> None:
    result = EvalResult(metric="acc", value=0.8, n=25)
    findings = [Finding(check="dup_rows", severity="high", detail="3 dupes")]
    text = build_report(
        _manifest(), result, 5, findings, [0.9, 0.85], p_value=0.01
    )
    assert "0.05" in _section(text, "Adjusted result")
    assert "0.1" in _section(text, "Adjusted result")
    audit = _section(text, "Audit findings")
    assert "high" in audit
    assert "dup_rows" in audit
    assert "—" not in text


def test_report_caps_adjusted_p_at_one() -> None:
    result = EvalResult(metric="acc", value=0.8, n=25)
    text = build_report(_manifest(), result, 50, [], [], p_value=0.04)
    assert "1.0" in _section(text, "Adjusted result")


def test_claim_runs_one_eval_and_reports_trials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    ledger = Ledger(store_dir(cfg) / "ledger.jsonl")
    ledger.append("trial", {"a": 1})
    ledger.append("trial", {"a": 2})
    text = claim(cfg, preds, [0.95, 0.9])
    assert "2" in _section(text, "Trials")
    assert "acc" in text
    assert "1.0" in text
    assert Ledger(store_dir(cfg) / "ledger.jsonl").eval_count() == 1
    assert load_manifest(cfg).metric == "acc"


def test_claim_second_call_budget_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    claim(cfg, preds, [0.9])
    with pytest.raises(BudgetExhausted):
        claim(cfg, preds, [0.9])


def test_claim_refuses_when_ledger_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    ledger = store_dir(cfg) / "ledger.jsonl"
    ledger.chmod(0o600)
    ledger.write_text(ledger.read_text().replace("seal", "seaL", 1))
    with pytest.raises(IntegrityError):
        claim(cfg, preds, [0.9])
    ledger.chmod(0o600)
    assert Ledger(ledger).eval_count() == 0


def _gap_line(text: str) -> str:
    return next(
        line for line in text.splitlines() if line.startswith("Selection gap:")
    )


def _gap_report(manifest_higher: bool, explicit: bool | None) -> str:
    result = EvalResult(metric="acc", value=0.7, n=25)
    text = build_report(
        _manifest(manifest_higher), result, 1, [], [0.8, 0.9],
        higher_is_better=explicit,
    )
    return _gap_line(text)


@pytest.mark.parametrize(
    ("manifest_higher", "expected"), [(True, "0.2"), (False, "-0.1")]
)
def test_report_none_uses_manifest_direction(
    manifest_higher: bool, expected: str
) -> None:
    assert _gap_report(manifest_higher, None) == f"Selection gap: {expected}"


def test_report_explicit_true_overrides_manifest_false() -> None:
    assert _gap_report(False, True) == "Selection gap: 0.2"


def test_report_explicit_false_overrides_manifest_true() -> None:
    assert _gap_report(True, False) == "Selection gap: -0.1"


@pytest.mark.parametrize(("p", "n"), [(True, 2), (0.5, True)])
def test_bonferroni_rejects_bool(p: float, n: int) -> None:
    with pytest.raises(ValueError):
        bonferroni(p, n)


def test_bonferroni_rejects_nan() -> None:
    with pytest.raises(ValueError):
        bonferroni(float("nan"), 2)


def test_bh_rejects_nan() -> None:
    with pytest.raises(ValueError):
        bh_adjust([float("nan")])


def test_selection_gap_rejects_inf() -> None:
    with pytest.raises(ValueError):
        selection_gap([float("inf")], 0.5)


def test_claim_nan_scores_rejected_before_eval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        claim(cfg, preds, validation_scores=[float("nan")])
    assert Ledger(store_dir(cfg) / "ledger.jsonl").eval_count() == 0
    text = claim(cfg, preds, [0.9])
    assert "acc" in text
    assert Ledger(store_dir(cfg) / "ledger.jsonl").eval_count() == 1


def test_claim_validation_scores_optional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, preds = _setup(tmp_path, monkeypatch)
    text = claim(cfg, preds)
    assert "Selection gap unavailable" in text
