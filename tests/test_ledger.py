import json
import multiprocessing
from pathlib import Path

from ml_referee.ledger import Ledger


def _worker(path: str, tag: str, n: int) -> None:
    ledger = Ledger(Path(path))
    for i in range(n):
        ledger.append("trial", {"tag": tag, "i": i})


def _lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


def _make(tmp_path: Path) -> tuple[Ledger, Path]:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append("trial", {"a": 1})
    ledger.append("trial", {"a": 2})
    ledger.append("eval", {"a": 3})
    return ledger, path


def test_append_chains_hashes(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    r1 = ledger.append("trial", {"x": 1})
    r2 = ledger.append("eval", {"x": 2})
    assert r1.index == 0
    assert r2.index == 1
    assert r1.prev_hash == "0" * 64
    assert r2.prev_hash == r1.hash
    assert len(r1.hash) == 64
    assert r1.hash != r2.hash
    assert r2.kind == "eval"
    assert r2.payload == {"x": 2}
    assert ledger.records() == [r1, r2]


def test_head_file_written(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append("trial", {"x": 1})
    r2 = ledger.append("trial", {"x": 2})
    assert (tmp_path / "head.txt").read_text().strip() == r2.hash


def test_verify_ok_on_clean_log(tmp_path: Path) -> None:
    ledger, _ = _make(tmp_path)
    result = ledger.verify()
    assert result.ok is True


def test_verify_fails_on_edited_payload(tmp_path: Path) -> None:
    ledger, path = _make(tmp_path)
    lines = _lines(path)
    obj = json.loads(lines[1])
    obj["payload"]["a"] = 999
    lines[1] = json.dumps(obj)
    path.write_text("\n".join(lines) + "\n")
    result = ledger.verify()
    assert result.ok is False
    assert "hash mismatch" in result.reason


def test_verify_fails_on_deleted_middle_record(tmp_path: Path) -> None:
    ledger, path = _make(tmp_path)
    lines = _lines(path)
    del lines[1]
    path.write_text("\n".join(lines) + "\n")
    assert ledger.verify().ok is False


def test_verify_fails_on_reordered_records(tmp_path: Path) -> None:
    ledger, path = _make(tmp_path)
    lines = _lines(path)
    lines[0], lines[1] = lines[1], lines[0]
    path.write_text("\n".join(lines) + "\n")
    assert ledger.verify().ok is False


def test_verify_fails_on_truncated_tail(tmp_path: Path) -> None:
    ledger, path = _make(tmp_path)
    lines = _lines(path)
    path.write_text("\n".join(lines[:-1]) + "\n")
    assert ledger.verify().ok is False


def test_trial_count_counts_only_trial_kind(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append("trial", {})
    ledger.append("trial", {})
    ledger.append("eval", {})
    ledger.append("seal", {})
    assert ledger.trial_count() == 2
    assert ledger.eval_count() == 1


def test_counts_zero_on_missing_file(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    assert ledger.trial_count() == 0
    assert ledger.eval_count() == 0
    assert ledger.records() == []


def test_concurrent_append_keeps_chain(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_worker, args=(str(path), t, 15)) for t in ("p", "q")]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
        assert p.exitcode == 0
    ledger = Ledger(path)
    recs = ledger.records()
    assert len(recs) == 30
    assert [r.index for r in recs] == list(range(30))
    assert ledger.verify().ok is True
    assert ledger.trial_count() == 30
