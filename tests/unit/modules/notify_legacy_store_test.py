"""Mixed-version store contract against the unmodified v0.9.29 implementation."""

import hashlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from heinzel import CapabilityState, Notice, NoticeKind, ResolveWhen
from heinzel.modules.system.notify import _resolve
from heinzel.modules.system.notify._store import MAX_NOTICES, Store

pytestmark = pytest.mark.unit
FIXTURE = Path(__file__).parents[2] / "fixtures" / "notify_v0929"
# Exact files from v0.9.29, peeled commit f034fb410ac801562c1afff68f62a19cec26a.
HASHES = {
    "notifications.py.txt": "65ebaa9b2d49885b93062ba3411031ab5e8e2db7be153483f5fddfbd535cce57",
    "store.py.txt": "8143f2e7d169271cf253e97f84694071e3824d556a41d72de2bce87eecdd9353",
}
OLD_WRITER = """
import sys, types
from datetime import UTC, datetime
from pathlib import Path
import heinzel
import heinzel.modules.system.notify._models
root, directory = map(Path, sys.argv[1:])
def load(name, filename):
    module = types.ModuleType(name)
    module.__file__ = str(root / filename)
    sys.modules[name] = module
    exec(compile((root / filename).read_bytes(), module.__file__, "exec"), module.__dict__)
    return module
old_notifications = load("old_notifications", "notifications.py.txt")
heinzel.Notice = old_notifications.Notice
old_store = load("old_store", "store.py.txt")
store = old_store.Store(directory)
assert len(store.read()) == 1
assert store.has_once("receipt")
def acknowledge(rows):
    rows[0] = rows[0].model_copy(update={"delivered_by": old_notifications.Delivery.WEB})
store.change(datetime.now(UTC), acknowledge)
assert store.has_once("receipt")
"""


def notice(now, key, condition):
    return Notice(
        id=key,
        kind=NoticeKind.DOCTOR,
        title="degraded",
        body="",
        route="/doctor",
        command="hz system doctor",
        key=key,
        at=now,
        due=now,
        popup=False,
        resolve_when=condition,
    )


@pytest.mark.parametrize("kind", ["plugin", "combined", "malformed", "unknown"])
def test_actual_v0929_reader_writer_preserves_conditions_and_receipt(tmp_path, kind):
    for name, expected in HASHES.items():
        assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() == expected
    conditions = {
        "plugin": ResolveWhen(plugins_installed=("sample",)),
        "combined": ResolveWhen(
            plugins_installed=("sample",),
            checks_up=["sample: network"],
            installed_version_at_least="0.9.29",
            capability=CapabilityState(operation="sample:status", field="ready", equals=True),
        ),
        "malformed": ResolveWhen(
            capability=CapabilityState(operation="system.notify:condition_v1", field="payload", equals="not-json")
        ),
        "unknown": ResolveWhen(
            capability=CapabilityState(
                operation="system.notify:condition_v2", field="payload", equals='{"checks_up":["sample: network"]}'
            )
        ),
    }
    condition = conditions[kind]
    now = datetime.now(UTC)
    store = Store(tmp_path)
    assert store.change_once(now, "receipt", lambda rows: rows.append(notice(now, "one", condition)))
    result = subprocess.run(
        [sys.executable, "-c", OLD_WRITER, str(FIXTURE), str(tmp_path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    (restored,) = store.read()
    assert restored.resolve_when == condition
    assert restored.delivered_by == "web"
    assert store.has_once("receipt")
    if kind in {"malformed", "unknown"}:
        assert _resolve.holds(condition, "999.0", lambda _: True) is None


def test_concurrent_writers_keep_distinct_envelopes_and_receipts(tmp_path):
    now = datetime.now(UTC)

    def write(index):
        condition = ResolveWhen(checks_up=[f"sample: check{index}"])
        return Store(tmp_path).change_once(
            now, f"receipt-{index}", lambda rows: rows.append(notice(now, str(index), condition))
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(write, range(24)))
    store = Store(tmp_path)
    assert {item.key: item.resolve_when.checks_up for item in store.read()} == {
        str(i): [f"sample: check{i}"] for i in range(24)
    }
    assert all(store.has_once(f"receipt-{i}") for i in range(24))


def test_pruning_retains_surviving_envelopes_and_replay_receipt(tmp_path):
    now = datetime.now(UTC)
    store = Store(tmp_path)
    condition = ResolveWhen(checks_up=["sample: network"])
    items = [notice(now - timedelta(days=31), "old", condition)] + [
        notice(now, str(i), condition) for i in range(MAX_NOTICES + 1)
    ]
    assert store.change_once(now, "receipt", lambda rows: rows.extend(items))
    restored = store.read()
    assert [item.key for item in restored] == [str(i) for i in range(1, MAX_NOTICES + 1)]
    assert all(item.resolve_when == condition for item in restored)
    assert store.has_once("receipt")
    assert store.change_once(now, "receipt", lambda rows: rows.clear())
    assert len(store.read()) == MAX_NOTICES


def test_age_pruning_removes_only_stale_envelope_below_capacity(tmp_path):
    now = datetime.now(UTC)
    store = Store(tmp_path)
    condition = ResolveWhen(checks_up=["sample: network"])
    stale = notice(now - timedelta(days=31), "stale", condition)
    fresh = notice(now, "fresh", condition)
    assert store.change_once(now, "receipt", lambda rows: rows.extend([stale, fresh]))
    restored = store.read()
    assert len(restored) < MAX_NOTICES
    assert [(item.key, item.resolve_when) for item in restored] == [("fresh", condition)]
    assert store.has_once("receipt")
