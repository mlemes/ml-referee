"""Hash-chained append-only ledger."""

import fcntl
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class Record:
    """Record type."""

    index: int
    kind: str
    payload: dict[str, object]
    time: str
    prev_hash: str
    hash: str


@dataclass(frozen=True)
class VerifyResult:
    """VerifyResult type."""

    ok: bool
    reason: str


class Ledger:
    """Hash-chained ledger."""

    def __init__(self, path: Path) -> None:
        """Open a ledger at path."""
        self.path = path

    def append(self, kind: str, payload: dict[str, object]) -> Record:
        """Append."""
        if kind not in {"trial", "eval", "seal"}:
            msg = f"Unknown record kind: {kind}"
            raise ValueError(msg)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.seek(0)
            records = [Record(**json.loads(line)) for line in stream]
            result = self._verify(records)
            if (
                not result.ok
                and result.reason == "Head hash mismatch"
                and len(records) >= 2  # noqa: PLR2004
                and self.path.with_name("head.txt").read_text(encoding="utf-8").strip()
                == records[-2].hash
            ):
                self._write_head(records[-1].hash)
                result = self._verify(records)
            if not result.ok:
                raise ValueError(result.reason)
            data = {
                "index": len(records),
                "kind": kind,
                "payload": payload,
                "time": datetime.now(UTC).isoformat(),
                "prev_hash": records[-1].hash if records else "0" * 64,
            }
            digest = self._digest(data)
            serialized = json.dumps({**data, "hash": digest}, sort_keys=True)
            stream.write(serialized + "\n")
            stream.flush()
            self._write_head(digest)
            return Record(**json.loads(serialized))

    def _write_head(self, digest: str) -> None:
        """Atomically replace the head with a private file under the ledger lock."""
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(digest + "\n")
            os.replace(temporary, self.path.with_name("head.txt"))  # noqa: PTH105
        finally:
            Path(temporary).unlink(missing_ok=True)

    def records(self) -> list[Record]:
        """Return all records."""
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            return [Record(**json.loads(line)) for line in stream]

    def verify(self) -> VerifyResult:
        """Verify."""
        try:
            if not self.path.exists():
                return self._verify([])
            with self.path.open(encoding="utf-8") as stream:
                fcntl.flock(stream, fcntl.LOCK_SH)
                return self._verify([Record(**json.loads(line)) for line in stream])
        except (ValueError, TypeError, OSError) as exc:
            return VerifyResult(ok=False, reason=f"Invalid ledger: {exc}")

    @staticmethod
    def _digest(data: dict[str, object]) -> str:
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _verify(self, records: list[Record]) -> VerifyResult:
        previous = "0" * 64
        for index, record in enumerate(records):
            data = asdict(record)
            del data["hash"]
            if self._digest(data) != record.hash:
                return VerifyResult(ok=False, reason=f"hash mismatch at record {index}")
            if record.index != index or record.prev_hash != previous:
                return VerifyResult(
                    ok=False, reason=f"Chain mismatch at record {index}",
                )
            previous = record.hash
        head = self.path.with_name("head.txt")
        if head.exists():
            if head.read_text(encoding="utf-8").strip() != previous:
                return VerifyResult(ok=False, reason="Head hash mismatch")
        elif records:
            return VerifyResult(ok=False, reason="Missing head hash")
        return VerifyResult(ok=True, reason="")

    def trial_count(self) -> int:
        """trial_count."""
        return sum(record.kind == "trial" for record in self.records())

    def eval_count(self) -> int:
        """eval_count."""
        return sum(record.kind == "eval" for record in self.records())
