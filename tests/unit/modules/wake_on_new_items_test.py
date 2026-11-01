"""A slot takes a turn only for items not seen before; the first read is the baseline. FAKE provider and journal."""

from types import SimpleNamespace

import pytest

from heinzel import ConnectedItemIdentity, HeinzelError
from heinzel.modules.heinzel._autonomous_schedule import AutonomousSchedules

pytestmark = pytest.mark.unit


class _Writer:
    def __init__(self, events):
        self.events = events

    def append(self, kind, key, data):
        self.events.append(SimpleNamespace(kind=kind, key=key, data=data))


class _Repository:
    principal_id = "u-" + "0" * 32

    def __init__(self):
        self.events = []
        self.writer = _Writer(self.events)

    def control_read(self, _session):
        return SimpleNamespace(events=self.events)

    def main_writer(self, _session):
        return _Ctx(self.writer)

    def control_writer(self, _session):
        return _Ctx(self.writer)


class _Ctx:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_exc):
        return False


def _schedules(items):
    repository = _Repository()
    crud = SimpleNamespace(repository=repository)
    grants = SimpleNamespace(read=lambda _session, _writer: items())
    chat = SimpleNamespace(connected_grants=lambda: grants)
    schedules = AutonomousSchedules(crud, chat)
    spec = ConnectedItemIdentity(collection=(), identity_field="id")
    binding = SimpleNamespace(connected_grant=SimpleNamespace(review=SimpleNamespace(item_identity=spec)))
    return schedules, binding, repository


def test_first_read_is_a_baseline_and_only_new_items_wake():
    current = [{"id": "a"}, {"id": "b"}]
    schedules, binding, repository = _schedules(lambda: current)
    assert schedules._new_items("s", binding) == {}
    assert sum(e.kind == "source.cursor" for e in repository.events) == 1
    assert schedules._new_items("s", binding) == {}
    current.append({"id": "c"})
    fresh = schedules._new_items("s", binding)
    assert len(fresh) == 1


def test_a_turn_that_died_sees_the_item_again_until_it_is_remembered():
    current = [{"id": "a"}]
    schedules, binding, _repository = _schedules(lambda: current)
    schedules._new_items("s", binding)
    current.append({"id": "b"})
    first = schedules._new_items("s", binding)
    assert schedules._new_items("s", binding) == first  # no turn finished, so the cursor did not move
    schedules._remember_items("s", set(first))
    assert schedules._new_items("s", binding) == {}


def test_a_source_without_identities_cannot_wake():
    schedules, binding, _repository = _schedules(list)
    binding.connected_grant.review.item_identity = None
    with pytest.raises(HeinzelError):
        schedules._new_items("s", binding)
