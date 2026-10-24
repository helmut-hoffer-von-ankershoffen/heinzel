import json
from datetime import UTC, datetime, timedelta

import pytest

from heinzel import HeinzelError
from heinzel.modules.system.daemon._ledger import Ledger, receipt_id
from heinzel.modules.system.daemon._models import Disposition, Wake

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
    )


def test_receipts_are_hash_chained_per_job(tmp_path):
    ledger = Ledger(tmp_path)
    first = append(ledger, 0)
    other = append(ledger, 1, job="example:other")
    second = append(ledger, 2)
    assert (first.prev, second.prev, other.prev) == (None, first.id, None)
    assert second.id == receipt_id(second)
    assert [r.id for r in Ledger(tmp_path).chain("example:digest")] == [first.id, second.id]


def test_one_file_per_receipt_named_by_time(tmp_path):
    ledger = Ledger(tmp_path)
    receipt = append(ledger, 0)
    [path] = tmp_path.glob("*.json")
    assert path.name == f"20261005T090001000000-{receipt.id[:16]}.json"
    assert json.loads(path.read_text(encoding="utf-8"))["id"] == receipt.id


def test_last_filters_by_wake_and_rendered_by_key(tmp_path):
    ledger = Ledger(tmp_path)
    scheduled = append(ledger, 0)
    append(ledger, 1, wake=Wake.MANUAL, disposition=Disposition.FAILED)
    assert ledger.last("example:digest") != scheduled
    assert ledger.last("example:digest", Wake.SCHEDULE) == scheduled
    assert ledger.last("example:none") is None
    assert ledger.rendered("example:digest", "example:digest@0")
    assert not ledger.rendered("example:digest", "example:digest@1")


def test_an_edited_receipt_is_refused(tmp_path):
    append(Ledger(tmp_path), 0)
    [path] = tmp_path.glob("*.json")
    path.write_text(path.read_text(encoding="utf-8").replace("rendered", "failed"), encoding="utf-8")
    with pytest.raises(HeinzelError, match="changed after it was written"):
        Ledger(tmp_path).chain("example:digest")


def test_an_unreadable_receipt_is_refused(tmp_path):
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(HeinzelError, match="unreadable"):
        Ledger(tmp_path).chain("example:digest")


def test_a_gap_in_the_chain_is_refused(tmp_path):
    ledger = Ledger(tmp_path)
    for n in range(3):
        append(ledger, n)
    sorted(tmp_path.glob("*.json"))[1].unlink()
    with pytest.raises(HeinzelError, match="chain of example:digest is broken"):
        Ledger(tmp_path).chain("example:digest")
