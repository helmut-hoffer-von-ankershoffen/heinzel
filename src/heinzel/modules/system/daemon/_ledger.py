"""The local ledger: one JSON file per receipt, hash-chained per job, never edited (ADR 0020).

The shared ledger in a GitHub state repository follows with the `state` module; until then receipts live in
`<data_dir>/receipts/`.

A receipt's id is the SHA-256 of its stored JSON without the id, so a field added in a later version is covered by
the receipts that have it and doesn't invalidate older ones. The hash is unkeyed: it detects accidents (a truncated
or hand-edited file, a lost receipt), not tampering by the user's own processes, which could recompute it.

Damage is contained per job: a job whose receipts don't verify or don't form one chain is reported as broken and
its jobs don't run, while every other job carries on.
"""

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue, ValidationError

from heinzel import HeinzelError
from heinzel.modules.system.daemon._models import Disposition, Receipt, Wake
from heinzel.modules.system.daemon._runtime import write_private


def receipt_id(stored: Mapping[str, Any]) -> str:
    """SHA-256 of a receipt's stored JSON (sorted keys, no whitespace), without its id."""
    content = {key: value for key, value in stored.items() if key != "id"}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class Ledger:
    """Reads and appends receipts in one directory; loads them once and keeps its view current as it appends."""

    def __init__(self, directory: Path) -> None:
        """Use `directory`; it is created on the first append."""
        self.directory = directory
        self._chains: dict[str, list[Receipt]] | None = None
        self._broken: dict[str, str] = {}
        self.unreadable: list[str] = []
        """Files that aren't receipts at all, so they can't be attributed to a job."""

    def broken(self) -> dict[str, str]:
        """Jobs whose receipts are damaged, with what is wrong."""
        self._load()
        return dict(self._broken)

    def chain(self, job: str) -> list[Receipt]:
        """The job's receipts, oldest first.

        Raises:
            HeinzelError: If the job's receipts are damaged.
        """
        chains = self._load()
        if job in self._broken:
            raise HeinzelError(self._broken[job])
        return list(chains.get(job, []))

    def last(self, job: str, wake: Wake | None = None) -> Receipt | None:
        """The job's newest receipt, or its newest with this wake cause.

        Raises:
            HeinzelError: If the job's receipts are damaged.
        """
        return next((r for r in reversed(self.chain(job)) if wake is None or r.wake is wake), None)

    def rendered(self, job: str, key: str) -> bool:
        """Whether a run with this idempotency key was rendered.

        Raises:
            HeinzelError: If the job's receipts are damaged.
        """
        return any(r.idempotency_key == key and r.disposition is Disposition.RENDERED for r in self.chain(job))

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
        result: JsonValue = None,
    ) -> Receipt:
        """Write a new receipt chained to the job's last one; the file name sorts by `written_at`.

        Raises:
            HeinzelError: If the job's receipts are damaged, since a new receipt can't be chained to them.
        """
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
            result=result,
        )
        stored = draft.model_dump(mode="json", exclude={"id"})
        stored = {"id": receipt_id(stored), **stored}
        receipt = draft.model_copy(update={"id": stored["id"]})
        path = self.directory / f"{written_at:%Y%m%dT%H%M%S%f}-{receipt.id[:16]}.json"
        write_private(path, json.dumps(stored, indent=2, ensure_ascii=False) + "\n")
        self._load().setdefault(job, []).append(receipt)
        return receipt

    def _load(self) -> dict[str, list[Receipt]]:
        if self._chains is None:
            by_job: dict[str, list[Receipt]] = defaultdict(list)
            for path in sorted(self.directory.glob("*.json")):
                self._read(path, by_job)
            self._chains = {}
            for job, receipts in by_job.items():
                if job in self._broken:
                    continue
                try:
                    self._chains[job] = _ordered(job, receipts)
                except HeinzelError as exc:
                    self._broken[job] = str(exc)
        return self._chains

    def _read(self, path: Path, by_job: dict[str, list[Receipt]]) -> None:
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.unreadable.append(path.name)
            return
        data = cast("dict[str, Any]", stored) if isinstance(stored, dict) else {}
        if not isinstance(data.get("job"), str):
            self.unreadable.append(path.name)
            return
        job = str(data["job"])
        if receipt_id(data) != data.get("id"):
            self._broken[job] = (
                f"Receipt {path} of {job} was changed after it was written; receipts are never edited. "
                f"Restore it, or move the job's receipts out of {path.parent} to start its chain again."
            )
            return
        try:
            by_job[job].append(Receipt.model_validate(data))
        except ValidationError as exc:
            self._broken[job] = f"Receipt {path} of {job} is not a valid receipt ({exc.error_count()} errors)."


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
        msg = (
            f"The receipt chain of {job} is broken: {len(receipts)} receipts, {len(ordered)} chained. "
            "Restore the missing receipts, or move the job's receipts aside to start its chain again."
        )
        raise HeinzelError(msg)
    return ordered
