"""Notices that resolve by themselves (ADR 0043, amended 2026-11-07): a condition holds, the notice disappears."""

import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel, ValidationError

from heinzel import CapabilityState, Module, Notice, NoticeKind, ResolveWhen
from heinzel.modules.system.notify import NotifyService, NotifySettings, _resolve
from heinzel.modules.system.notify._native import Native
from heinzel.modules.system.notify._watch import watch

pytestmark = pytest.mark.unit

BERLIN = ZoneInfo("Europe/Berlin")
ORG = {"working_hours": "09-18", "timezone": "Europe/Berlin"}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 21, 12, 0, tzinfo=BERLIN)

    def __call__(self) -> datetime:
        return self.now


def _runner(command, timeout=None):
    return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(settings, clock) -> NotifyService:
    made = NotifyService(
        settings,
        NotifySettings(release=True),
        ORG,
        clock=clock,
        notifier=Native("notify-send", "/usr/bin/notify-send", detail="through notify-send", runner=_runner),
    )
    made.installed = lambda: "0.9.27"
    return made


def release(service: NotifyService, latest: str = "0.9.28", *, condition: bool = True) -> Notice | None:
    return service.emit(
        NoticeKind.RELEASE,
        f"hz {latest} is available",
        "You have 0.9.27.",
        route="/doctor",
        command="hz system update",
        key=f"release:{latest}",
        resolve_when=ResolveWhen(installed_version_at_least=latest) if condition else None,
    )


def shown(service: NotifyService) -> list[str]:
    return [notice.title for notice in service.listing(50).notices]


def test_an_update_notice_disappears_once_its_version_is_installed(service):
    assert release(service) is not None
    assert shown(service) == ["hz 0.9.28 is available"]
    service.installed = lambda: "0.9.28"
    assert shown(service) == []  # at once, on read: no tick needed for a version condition
    assert service.recent() == []
    assert service.due() == []


def test_resolving_keeps_the_history_with_a_reason_and_never_marks_read(service, clock):
    notice = release(service)
    assert notice is not None
    service.installed = lambda: "0.9.29"
    clock.now += timedelta(minutes=5)
    assert service.resolve() == 1
    assert service.resolve() == 0  # once
    (kept,) = service.listing(50, resolved=True).notices
    assert (kept.id, kept.resolved_at, kept.resolved) == (notice.id, clock.now, "hz 0.9.29 is installed")
    assert (kept.delivered_by, kept.delivered_at) == (None, None)  # read stays the person's own action


def test_a_notice_whose_condition_already_holds_is_never_recorded(service):
    service.installed = lambda: "0.9.28"
    assert release(service) is None
    assert service.listing(50, resolved=True).notices == []


def test_a_legacy_release_notice_without_a_condition_resolves_by_its_key(service):
    """What an older process recorded (no condition) still disappears once its version is installed."""
    assert release(service, condition=False) is not None
    service.installed = lambda: "0.9.28"
    assert shown(service) == []


def test_recover_finds_and_with_apply_resolves_stale_update_notices(service):
    release(service, condition=False)
    service.installed = lambda: "0.9.28"
    found = service.recover(apply=False)
    assert (found.state, found.resolved) == ("healthy", 1)
    assert "--apply" in found.detail
    assert service.listing(50, resolved=True).notices[0].resolved is None  # a dry run writes nothing
    applied = service.recover(apply=True)
    assert (applied.state, applied.resolved) == ("healthy", 1)
    assert service.listing(50, resolved=True).notices[0].resolved == "hz 0.9.28 is installed"
    assert service.recover(apply=True).resolved == 0


def test_a_capability_condition_is_asked_at_a_tick_only_and_once_per_pass(service):
    asked: list[CapabilityState] = []
    state = {"loaded": False}

    def probe(capability: CapabilityState) -> bool | None:
        asked.append(capability)
        return state[capability.field] == capability.equals

    service.capability = probe
    capability = CapabilityState(operation="system.daemon:status", field="loaded", equals=True)
    for key in ("a", "b"):
        service.emit(
            NoticeKind.DOCTOR,
            "The daemon is not loaded",
            route="/doctor",
            command="hz system daemon install",
            key=key,
            resolve_when=ResolveWhen(capability=capability),
        )
    asked.clear()
    assert len(shown(service)) == 2
    assert asked == []  # a read never runs another module's operation
    state["loaded"] = True
    assert service.resolve() == 2
    assert asked == [capability]
    assert shown(service) == []


def test_a_capability_that_cannot_answer_never_resolves(service):
    service.capability = lambda capability: None
    capability = CapabilityState(operation="weather:now", field="state", equals="clear")
    service.emit(
        NoticeKind.PLUGIN,
        "Storm",
        route="/",
        command="hz weather now",
        key="w",
        resolve_when=ResolveWhen(capability=capability),
    )
    assert service.resolve() == 0
    assert shown(service) == ["Storm"]


def test_every_condition_given_must_hold(service):
    state = {"loaded": False}
    service.capability = lambda capability: state["loaded"]
    when = ResolveWhen(
        installed_version_at_least="0.9.28",
        capability=CapabilityState(operation="system.daemon:status", field="loaded", equals=True),
    )
    service.emit(NoticeKind.DOCTOR, "Both", route="/doctor", command="hz", key="both", resolve_when=when)
    service.installed = lambda: "0.9.28"
    assert service.resolve() == 0
    state["loaded"] = True
    assert service.resolve() == 1


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"installed_version_at_least": "latest"},
        {"capability": {"operation": "no-colon", "field": "x", "equals": 1}},
        {"capability": {"operation": "a:b", "field": "Bad Field", "equals": 1}},
        {"installed_version_at_least": "1.0", "extra": 1},
    ],
)
def test_a_condition_is_declarative_and_closed(values):
    with pytest.raises(ValidationError):
        ResolveWhen.model_validate(values)


class Status(BaseModel):
    loaded: bool


def test_the_kernel_probe_reads_a_read_only_operation_by_name(make_kernel):
    probes = Module("probes", "Test capability states.")
    loaded = {"value": True}

    @probes.operation()
    def status() -> Status:
        """How it stands."""
        return Status(loaded=loaded["value"])

    @probes.operation(read_only=False)
    def change() -> Status:
        """Writes."""
        return Status(loaded=True)

    probe = _resolve.kernel_probe(make_kernel(probes=probes))
    assert probe(CapabilityState(operation="probes:status", field="loaded", equals=True)) is True
    assert probe(CapabilityState(operation="probes:status", field="loaded", equals="true")) is False  # typed
    assert probe(CapabilityState(operation="probes:status", field="missing", equals=True)) is None
    assert probe(CapabilityState(operation="probes:change", field="loaded", equals=True)) is None  # not read-only
    assert probe(CapabilityState(operation="nowhere:status", field="loaded", equals=True)) is None


def test_the_watcher_compares_the_installed_version_not_the_running_process(service, make_kernel, monkeypatch):
    """A process started before an update must not announce the version that is installed now."""
    kernel = make_kernel()
    monkeypatch.setattr(
        "heinzel.modules.system.notify._watch.ReleaseCache.newer_than",
        lambda self, version, channel: "0.9.28" if version == "0.9.27" else None,
    )
    monkeypatch.setattr("heinzel.modules.system.notify._watch.installed_version", lambda: "0.9.28")
    assert watch(kernel, service, {NoticeKind.RELEASE}) == 0
    monkeypatch.setattr("heinzel.modules.system.notify._watch.installed_version", lambda: "0.9.27")
    assert watch(kernel, service, {NoticeKind.RELEASE}) == 1
    (notice,) = service.listing(50).notices
    assert notice.body.startswith("You have 0.9.27;")
    assert notice.resolve_when == ResolveWhen(installed_version_at_least="0.9.28")


def doctor(service: NotifyService, problems: list[str], *, condition: bool = True) -> Notice | None:
    return service.emit(
        NoticeKind.DOCTOR,
        "Heinzelmännchen is degraded",
        f"{len(problems)} check(s) need you: {', '.join(problems)}",
        route="/doctor",
        command="hz system doctor",
        key=f"doctor:degraded:{'|'.join(problems)}",
        resolve_when=ResolveWhen(checks_up=problems) if condition else None,
    )


@pytest.mark.parametrize("condition", [True, False], ids=["with condition", "recorded before conditions"])
def test_a_doctor_notice_disappears_once_its_checks_are_up_again(service, condition):
    failing: set[str] | None = {"system.credential: weather/api-key", "org: calendar"}
    asked: list[int] = []

    def problems() -> set[str] | None:
        asked.append(1)
        return failing

    service.problems = problems
    assert doctor(service, ["system.credential: weather/api-key"], condition=condition) is not None
    assert shown(service) == ["Heinzelmännchen is degraded"]
    assert asked == []  # a read never runs doctor
    assert service.resolve() == 0
    failing = None  # doctor can't tell: nothing resolves
    assert service.resolve() == 0
    failing = {"org: calendar"}  # another check is still degraded; the named one is up
    asked.clear()
    assert service.resolve() == 1
    assert asked == [1]
    assert shown(service) == []
    (kept,) = service.listing(50, resolved=True).notices
    assert kept.resolved == "system.credential: weather/api-key is up again"
    assert kept.delivered_by is None  # never marked read


def test_the_doctor_watcher_attaches_its_checks_as_the_condition(service, make_kernel, monkeypatch):
    from heinzel import Status as Health

    monkeypatch.setattr(
        "heinzel.modules.system.notify._watch._report",
        lambda kernel: (Health.DEGRADED, ["system.credential: weather/api-key"]),
    )
    assert watch(make_kernel(), service, {NoticeKind.DOCTOR}) == 1
    (notice,) = service.listing(50).notices
    assert notice.resolve_when == ResolveWhen(checks_up=["system.credential: weather/api-key"])
