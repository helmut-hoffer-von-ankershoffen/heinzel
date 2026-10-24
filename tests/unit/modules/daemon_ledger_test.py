import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import Field

from heinzel import HeinzelError
from heinzel.modules.system.daemon import _ledger
from heinzel.modules.system.daemon._ledger import Ledger, receipt_id
from heinzel.modules.system.daemon._models import Disposition, Receipt, Wake

pytestmark = pytest.mark.unit

T0 = datetime(2026, 10, 5, 9, tzinfo=UTC)


def append(
    ledger: Ledger, n: int, *, job: str = "example:digest", disposition=Disposition.RENDERED, wake=Wake.SCHEDULE
):
    return ledger.append(
        job=job,
        wake=wake,
        disposition=disposition,
        as_of=T0 + timedelta(hours=n),
        idempotency_key=f"{job}@{n}",
        error=None,
        written_at=T0 + timedelta(hours=n, seconds=1),
        result={"n": n},
    )


def test_receipts_are_hash_chained_per_job(tmp_path):
    ledger = Ledger(tmp_path)
    first = append(ledger, 0)
    other = append(ledger, 1, job="example:other")
    second = append(ledger, 2)
    assert (first.prev, second.prev, other.prev) == (None, first.id, None)
    assert second.id == receipt_id(second.model_dump(mode="json"))
    chain = Ledger(tmp_path).chain("example:digest")
    assert [r.id for r in chain] == [first.id, second.id]
    assert chain[1].result == {"n": 2}


def test_the_id_is_the_hash_of_the_stored_json_without_it(tmp_path):
    receipt = append(Ledger(tmp_path), 0)
    [path] = tmp_path.glob("*.json")
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["id"] == receipt.id == receipt_id(stored)
    assert path.name == f"20261005T090001000000-{receipt.id[:16]}.json"


def test_adding_a_field_to_receipts_keeps_old_chains_valid(tmp_path, monkeypatch):
    first = append(Ledger(tmp_path), 0)

    class Later(Receipt):
        runner: str | None = Field(default=None, description="Added in a later version.")

    monkeypatch.setattr(_ledger, "Receipt", Later)
    ledger = Ledger(tmp_path)
    assert [r.id for r in ledger.chain("example:digest")] == [first.id]
    second = append(ledger, 1)
    assert "runner" in json.loads(max(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert [r.id for r in Ledger(tmp_path).chain("example:digest")] == [first.id, second.id]


def test_last_filters_by_wake_and_rendered_by_key(tmp_path):
    ledger = Ledger(tmp_path)
    scheduled = append(ledger, 0)
    append(ledger, 1, wake=Wake.MANUAL, disposition=Disposition.FAILED)
    assert ledger.last("example:digest") != scheduled
    assert ledger.last("example:digest", Wake.SCHEDULE) == scheduled
    assert ledger.last("example:none") is None
    assert ledger.rendered("example:digest", "example:digest@0")
    assert not ledger.rendered("example:digest", "example:digest@1")


def test_an_edited_receipt_breaks_only_its_job(tmp_path):
    ledger = Ledger(tmp_path)
    append(ledger, 0)
    append(ledger, 1, job="example:other")
    path = next(p for p in tmp_path.glob("*.json") if '"example:digest"' in p.read_text(encoding="utf-8"))
    path.write_text(path.read_text(encoding="utf-8").replace("rendered", "failed"), encoding="utf-8")
    fresh = Ledger(tmp_path)
    with pytest.raises(HeinzelError, match="changed after it was written"):
        fresh.chain("example:digest")
    assert len(fresh.chain("example:other")) == 1
    assert set(fresh.broken()) == {"example:digest"}


def test_an_invalid_receipt_breaks_only_its_job(tmp_path):
    stored = {"job": "example:digest", "wake": "never"}
    stored = {"id": receipt_id(stored), **stored}
    (tmp_path / "bad.json").write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(HeinzelError, match="not a valid receipt"):
        Ledger(tmp_path).chain("example:digest")


def test_files_that_are_not_receipts_are_listed_and_ignored(tmp_path):
    append(Ledger(tmp_path), 0)
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    (tmp_path / "list.json").write_text("[1]", encoding="utf-8")
    ledger = Ledger(tmp_path)
    assert len(ledger.chain("example:digest")) == 1
    assert sorted(ledger.unreadable) == ["broken.json", "list.json"]


def test_a_gap_in_the_chain_breaks_its_job(tmp_path):
    ledger = Ledger(tmp_path)
    for n in range(3):
        append(ledger, n)
    sorted(tmp_path.glob("*.json"))[1].unlink()
    with pytest.raises(HeinzelError, match="chain of example:digest is broken"):
        Ledger(tmp_path).chain("example:digest")
