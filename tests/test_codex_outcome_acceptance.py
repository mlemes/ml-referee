"""Acceptance tests for outcomes observable through the recorder CLI."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ml_referee.config import load_config, store_dir
from ml_referee.ledger import Ledger
from ml_referee.seal import seal, verify_manifest


@pytest.fixture
def sealed_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    work = tmp_path / "workspace"
    work.mkdir()
    (work / "train.csv").write_text("x,y\n1,0\n2,1\n3,0\n4,1\n")
    config_path = work / ".ml-referee.toml"
    config_path.write_text(
        '[project]\nid = "outcome-acceptance"\n'
        '[data]\ntrain = "train.csv"\nlabel = "y"\n'
        '[eval]\ncommand = "python eval.py"\nmetric = "acc"\n'
        'higher_is_better = true\n'
        '[trials]\npatterns = ["python train.py*"]\n'
    )
    evaluator = work / "eval.py"
    evaluator.write_text("print(0.5)\n")
    cfg = load_config(config_path)
    seal(cfg, evaluator)
    ledger = Ledger(store_dir(cfg) / "ledger.jsonl")
    assert verify_manifest(cfg)
    assert ledger.verify().ok
    assert ledger.trial_count() == 0
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return work, cfg, ledger, env


def _record(project, response, *, harness="codex", event="PostToolUse", tag="trial"):
    work, cfg, ledger, env = project
    command = f"python train.py --{tag}"
    payload = {
        "hook_event_name": event,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": response,
        "cwd": str(work),
        "turn_id": "synthetic",
    }
    before = ledger.trial_count()
    proc = subprocess.run(
        [sys.executable, "-m", "ml_referee.hooks.recorder", "--harness", harness],
        input=json.dumps(payload), text=True, cwd=work, env=env,
        capture_output=True, timeout=20, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == "", proc.stderr
    assert ledger.verify().ok, ledger.verify().reason
    assert ledger.trial_count() == before + 1
    records = ledger.records()
    assert len(records) == before + 2  # the seal plus each recorded trial
    assert records[0].kind == "seal"
    assert records[-1].kind == "trial"
    result = records[-1].payload
    assert result["command"] == command
    assert result["train_sha256"] == hashlib.sha256(cfg.train_path.read_bytes()).hexdigest()
    assert verify_manifest(cfg)
    return result


@pytest.mark.parametrize(
    "response",
    [
        pytest.param("TRAIN_FAILED\n", id="live-failure-output"),
        pytest.param("TRAIN_OK\n", id="success-looking-output"),
        pytest.param("Process exited with code 7\n", id="claimed-nonzero"),
        pytest.param("Process exited with code 0\n", id="claimed-zero"),
        pytest.param('{"exit_code": 7}\n', id="json-looking-stdout"),
        pytest.param({"stdout": "exit_code: 0\n"}, id="stdout-field-without-status"),
    ],
)
def test_codex_without_authoritative_status_is_unknown(sealed_project, response):
    result = _record(sealed_project, response, tag="fail")
    assert result["exit_code"] is None
    assert result["failed"] is None


@pytest.mark.parametrize("harness", ["codex", "claude"])
@pytest.mark.parametrize("exit_code, failed", [(0, False), (7, True)])
def test_structured_status_preserves_known_outcome(
    sealed_project, harness, exit_code, failed,
):
    # Conflicting stdout must not override structured process metadata.
    response = {"exit_code": exit_code, "stdout": f"exit_code: {7 if exit_code == 0 else 0}"}
    result = _record(sealed_project, response, harness=harness)
    assert result["exit_code"] == exit_code
    assert result["failed"] is failed


@pytest.mark.parametrize("harness", ["codex", "claude"])
def test_failure_event_is_known_failure_without_exit_code(sealed_project, harness):
    result = _record(
        sealed_project, "exit_code: 0\n", harness=harness, event="PostToolUseFailure",
    )
    assert result["exit_code"] is None
    assert result["failed"] is True


def test_claude_plain_post_keeps_existing_success_semantics(sealed_project):
    result = _record(sealed_project, "ok\n", harness="claude")
    assert result["exit_code"] is None
    assert result["failed"] is False


def test_mixed_codex_outcomes_all_count_as_trials_in_valid_ledger(sealed_project):
    _record(sealed_project, "TRAIN_FAILED\n", tag="unknown")
    _record(sealed_project, {"exit_code": 0}, tag="success")
    _record(sealed_project, {"exit_code": 7}, tag="failure")
    _record(sealed_project, "error", event="PostToolUseFailure", tag="tool-failure")
    _, _, ledger, _ = sealed_project
    assert ledger.trial_count() == 4
    assert ledger.eval_count() == 0
    assert ledger.verify().ok
