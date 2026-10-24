"""The local ledger: one JSON file per receipt, hash-chained per job, never edited (ADR 0020).

The shared ledger in a GitHub state repository follows with the `state` module; until then receipts live in
`<data_dir>/receipts/`.
"""

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from heinzel import HeinzelError
from heinzel.modules.system.daemon._models import Disposition, Receipt, Wake


def receipt_id(receipt: Receipt) -> str:
    """SHA-256 of the receipt's canonical JSON (sorted keys, no whitespace), without its id."""
    canonical = json.dumps(receipt.model_dump(mode="json", exclude={"id"}), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class Ledger:
    """Reads and appends receipts in one directory; loads them once and keeps its view current as it appends."""

    def __init__(self, directory: Path) -> None:
        """Use `directory`; it is created on the first append."""
        self.directory = directory
        self._chains: dict[str, list[Receipt]] | None = None

    def chain(self, job: str) -> list[Receipt]:
        """The job's receipts, oldest first."""
        return list(self._load().get(job, []))

    def last(self, job: str, wake: Wake | None = None) -> Receipt | None:
        """The job's newest receipt, or its newest with this wake cause."""
        return next((r for r in reversed(self._load().get(job, [])) if wake is None or r.wake is wake), None)

    def rendered(self, job: str, key: str) -> bool:
        """Whether a run with this idempotency key was rendered."""
        return any(
            r.idempotency_key == key and r.disposition is Disposition.RENDERED for r in self._load().get(job, [])
        )

    def append(  # noqa: PLR0913 - the fields of a receipt
        self,
        *,
        job: str,
        wake: Wake,
        disposition: Disposition,
        as_of: datetime,
        idempotency_key: str,
        error: str | None,
        written_at: datetime,
    ) -> Receipt:
        """Write a new receipt chained to the job's last one; the file name sorts by `written_at`."""
        previous = self.last(job)
        draft = Receipt(
            id="",
            prev=previous.id if previous else None,
            job=job,
            wake=wake,
            disposition=disposition,
            as_of=as_of,
            idempotency_key=idempotency_key,
            error=error,
        )
        receipt = draft.model_copy(update={"id": receipt_id(draft)})
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{written_at:%Y%m%dT%H%M%S%f}-{receipt.id[:16]}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(receipt.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)  # atomic, so a crash never leaves half a receipt
        self._load().setdefault(job, []).append(receipt)
        return receipt

    def _load(self) -> dict[str, list[Receipt]]:
        if self._chains is None:
            by_job: dict[str, list[Receipt]] = defaultdict(list)
            for path in sorted(self.directory.glob("*.json")):
                receipt = _read(path)
                by_job[receipt.job].append(receipt)
            self._chains = {job: _ordered(job, receipts) for job, receipts in by_job.items()}
        return self._chains


def _read(path: Path) -> Receipt:
    try:
        receipt = Receipt.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        msg = f"Receipt {path} is unreadable ({exc}); move it out of {path.parent} to continue."
        raise HeinzelError(msg) from exc
    if receipt_id(receipt) != receipt.id:
        msg = f"Receipt {path} was changed after it was written; receipts are never edited. Restore or move it."
        raise HeinzelError(msg)
    return receipt


def _ordered(job: str, receipts: list[Receipt]) -> list[Receipt]:
    """Follow the chain from the first receipt; a fork or a gap means receipts were lost or copied in.

    Raises:
        HeinzelError: If the receipts don't form one chain.
    """
    by_prev = {r.prev: r for r in receipts}
    ordered: list[Receipt] = []
    current = by_prev.get(None)
    while current is not None:
        ordered.append(current)
        current = by_prev.get(current.id)
    if len(ordered) != len(receipts) or len(by_prev) != len(receipts):
        msg = f"The receipt chain of {job} is broken: {len(receipts)} receipts, {len(ordered)} chained."
        raise HeinzelError(msg)
    return ordered
