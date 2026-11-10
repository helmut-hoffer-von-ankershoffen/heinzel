"""Notices that resolve by themselves (ADR 0043, amended 2026-11-07): a condition holds, the notice disappears."""

import json
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


def test_ordinary_conditions_are_compatible_with_older_store_readers(service):
    class OlderCondition(BaseModel, extra="forbid"):
        installed_version_at_least: str | None = None
        capability: dict | None = None
        plugins_installed: list[str] | None = None

    assert release(service) is not None
    raw = json.loads(service.store.path.read_text())
    OlderCondition.model_validate(raw[0]["resolve_when"])


@pytest.mark.parametrize("durable", [False, True])
def test_check_condition_survives_legacy_reader_and_writer(service, clock, durable):
    class OlderCondition(BaseModel, extra="forbid"):
        installed_version_at_least: str | None = None
        capability: CapabilityState | None = None
        plugins_installed: tuple[str, ...] | None = None

    condition = ResolveWhen(
        checks_up=["sample: first", "another: second"],
        capability=CapabilityState(operation="sample:status", field="ready", equals=True),
        installed_version_at_least="0.9.28",
        plugins_installed=("example",),
    )
    notice = service.emit(
        NoticeKind.DOCTOR,
        "degraded",
        "",
        route="/doctor",
        command="hz system doctor",
        key="arbitrary",
        resolve_when=condition,
    )
    assert notice
    if durable:
        service.store.change_once(clock(), "receipt", lambda notices: None)
    raw = json.loads(service.store.path.read_text())
    notices = raw["notices"] if durable else raw
    old = OlderCondition.model_validate(notices[0]["resolve_when"])
    assert old.capability.operation == "system.notify:condition_v1"
    # An older acknowledgement rewrites the strict, known schema.
    notices[0]["resolve_when"] = old.model_dump(mode="json")
    notices[0]["delivered_by"] = "web"
    service.store.path.write_text(json.dumps(raw))
    restored = service.store.read()[0]
    assert restored.resolve_when == condition
    assert restored.delivered_by == "web"
    if durable:
        assert service.store.has_once("receipt")


@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        ("system.notify:condition_v1", "not-json"),
        ("system.notify:condition_v2", '{"checks_up":["sample: first"]}'),
        ("system.notify:condition_v1", '{"checks_up":[]}'),
    ],
)
def test_unknown_or_invalid_condition_envelope_is_preserved_and_never_resolves(service, operation, payload):
    when = ResolveWhen(capability=CapabilityState(operation=operation, field="payload", equals=payload))
    notice = service.emit(
        NoticeKind.DOCTOR,
        "degraded",
        "",
        route="/doctor",
        command="hz system doctor",
        key="arbitrary",
        resolve_when=when,
    )
    assert notice
    assert service.store.read()[0].resolve_when == when
    assert _resolve.holds(when, "999.0", lambda capability: True) is None
    service.store.change(service._clock(), lambda notices: None)
    assert service.store.read()[0].resolve_when == when


def test_background_health_resolution_respects_consent_and_hourly_limit(service, clock):
    calls = []
    service.problems = lambda: calls.append(1) or set()
    assert doctor(service, ["sample: network"])
    service.settings = service.settings.model_copy(update={"doctor": False})
    service.post_native()
    assert calls == []
    service.settings = service.settings.model_copy(update={"doctor": True})
    service.post_native()
    service.post_native()
    assert calls == [1]
    clock.now += timedelta(hours=1)
    service.post_native()
    assert calls == [1, 1]


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
    failing: set[str] | None = {"org: calendar"}
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
    failing = {"system.credential: weather/api-key"}  # only explicitly UP checks resolve
    asked.clear()
    assert service.resolve() == 1
    assert asked == [1]
    assert shown(service) == []
    (kept,) = service.listing(50, resolved=True).notices
    assert kept.resolved == "system.credential: weather/api-key is up again"
    assert kept.delivered_by is None  # never marked read


def test_provider_exception_does_not_resolve_its_named_check(service, make_kernel):
    from heinzel import Check, Status
    from heinzel.modules.system.doctor import module as doctor_module
    from heinzel.modules.system.notify._watch import doctor_problems

    provider = Module("sample", "Sample health provider.")
    broken = {"value": True}

    @provider.health
    def health() -> list[Check]:
        """Report health, or fail before reporting the named check."""
        if broken["value"]:
            message = "provider failed"
            raise RuntimeError(message)
        return [Check(name="network", status=Status.UP, detail="connected")]

    kernel = make_kernel(system__doctor=doctor_module, sample=provider)
    service.problems = lambda: doctor_problems(kernel)
    assert doctor(service, ["sample: network"])
    assert service.resolve() == 0
    assert len(service.recent()) == 1
    broken["value"] = False
    assert service.resolve() == 1


def test_a_recurring_fault_creates_a_new_visible_notice(service):
    failing: set[str] = set()
    service.problems = lambda: failing
    first = doctor(service, ["sample: network"])
    assert first is not None
    failing.add("sample: network")
    assert service.resolve() == 1
    failing.clear()
    second = doctor(service, ["sample: network"])
    assert second is not None
    assert second.id != first.id
    assert [notice.id for notice in service.recent()] == [second.id]
    assert len(service.listing(50, resolved=True).notices) == 2


def test_plugin_and_doctor_conditions_compose_without_extra_doctor_probes(service, monkeypatch):
    installed: set[str] = set()
    failing: set[str] | None = None
    asked: list[int] = []

    def problems() -> set[str] | None:
        asked.append(1)
        return failing

    service.problems = problems
    monkeypatch.setattr(_resolve, "plugin_installed", installed.__contains__)
    assert doctor(service, ["system.daemon: jobs"], condition=False) is not None
    condition = ResolveWhen(plugins_installed=("heinzel-needed",))
    assert service.emit(
        NoticeKind.PLUGIN, "Plugin needed", route="/", command="hz", key="plugin", resolve_when=condition
    )
    both = ResolveWhen(plugins_installed=("heinzel-needed",), checks_up=["org: calendar"])
    assert service.emit(NoticeKind.DOCTOR, "Both", route="/doctor", command="hz", key="both", resolve_when=both)

    installed.add("heinzel-needed")
    assert set(shown(service)) == {"Both", "Heinzelmännchen is degraded"}
    assert asked == []  # disk-only conditions still hide immediately
    assert service.resolve() == 1  # the plugin is installed, but doctor cannot answer
    assert asked == [1]  # two unresolved doctor notices share the unavailable result
    failing = {"org: calendar", "system.daemon: jobs"}
    assert service.resolve() == 2
    assert asked == [1, 1]
    failing = {"org: calendar", "system.daemon: jobs"}
    assert service.resolve() == 0
    assert asked == [1, 1]
    assert shown(service) == []  # resolved notices stay resolved


def test_the_doctor_watcher_attaches_its_checks_as_the_condition(service, make_kernel, monkeypatch):
    from heinzel import Status as Health

    monkeypatch.setattr(
        "heinzel.modules.system.notify._watch._report",
        lambda kernel: (Health.DEGRADED, ["system.credential: weather/api-key"], set()),
    )
    assert watch(make_kernel(), service, {NoticeKind.DOCTOR}) == 1
    (notice,) = service.listing(50).notices
    assert notice.resolve_when == ResolveWhen(checks_up=["system.credential: weather/api-key"])


def legacy_doctor(key: str) -> Notice:
    """A doctor notice as the live store kept it on 2026-11-08: no `resolve_when`."""
    return Notice.model_validate(
        {
            "id": "20261108T042026737329Z-fe646a",
            "kind": "doctor",
            "title": "Heinzelmännchen is degraded",
            "body": "1 check(s) need you: system.daemon: jobs",
            "route": "/doctor",
            "command": "hz system doctor",
            "key": key,
            "urgent": False,
            "at": "2026-11-08T04:20:26.737329Z",
            "due": "2026-11-09T09:00:00+01:00",
            "expires": None,
            "popup": True,
            "held": None,
            "helper": None,
            "delivered_by": None,
            "delivered_at": None,
            "resolve_when": None,
            "resolved_at": None,
            "resolved": None,
        }
    )


@pytest.mark.parametrize(
    ("key", "checks"),
    [
        # The exact keys of the stale notices seen on Today on 2026-11-08.
        (
            "doctor:degraded:system.credential: credential weather/api-key",
            ["system.credential: credential weather/api-key"],
        ),
        ("doctor:degraded:system.daemon: jobs", ["system.daemon: jobs"]),
        ("doctor:down:org: calendar|system.daemon: jobs", ["org: calendar", "system.daemon: jobs"]),
    ],
)
def test_a_legacy_doctor_key_implies_its_checks(key, checks):
    notice = legacy_doctor(key)
    assert _resolve.condition(notice) == ResolveWhen(checks_up=checks)


@pytest.mark.parametrize("key", ["doctor:degraded:", "doctor:degraded", "doctor:degraded:no colon here"])
def test_a_malformed_doctor_key_never_resolves(key):
    notice = legacy_doctor(key)
    assert _resolve.condition(notice) is None


@pytest.mark.parametrize("status", ["up", "degraded"])
def test_the_doctor_watcher_reports_nothing_without_a_failing_check(service, make_kernel, monkeypatch, status):
    from heinzel import Status as Health

    monkeypatch.setattr("heinzel.modules.system.notify._watch._report", lambda kernel: (Health(status), [], set()))
    assert watch(make_kernel(), service, {NoticeKind.DOCTOR}) == 0
    assert service.listing(50).notices == []


def test_resolving_a_doctor_notice_logs_one_structured_event(service, caplog):
    failing: set[str] = set()
    service.problems = lambda: failing
    notice = doctor(service, ["system.daemon: jobs"])
    assert notice is not None
    failing = {"system.daemon: jobs"}
    with caplog.at_level("INFO", logger="heinzel.system.notify"):
        assert service.resolve() == 1
        assert service.resolve() == 0  # already resolved: no second event
    (event,) = [record for record in caplog.records if record.getMessage() == "notify.resolve.notice"]
    assert event.operation == "system.notify:resolve"
    assert event.outcome == "ok"
    assert event.notice == notice.id
    assert event.reason == "system.daemon: jobs is up again"


def test_a_failing_doctor_probe_resolves_nothing_and_logs_the_error(service, make_kernel, monkeypatch, caplog):
    from heinzel.modules.system.notify._watch import doctor_problems

    def broken(kernel):
        message = "doctor exploded"
        raise RuntimeError(message)

    monkeypatch.setattr("heinzel.modules.system.notify._watch._report", broken)
    with caplog.at_level("WARNING", logger="heinzel.system.notify"):
        assert doctor_problems(make_kernel()) is None
    (event,) = [record for record in caplog.records if record.getMessage() == "notify.resolve.doctor"]
    assert event.operation == "system.notify:resolve"
    assert event.step == "doctor"
    assert event.outcome == "error"
    assert event.error_type == "RuntimeError"
    assert event.exc_info is not None


def test_noncanonical_envelope_with_sibling_is_never_decoded_or_resolved(service):
    payload = ResolveWhen(checks_up=["sample: network"]).model_dump_json(exclude_none=True)
    when = ResolveWhen(
        capability=CapabilityState(operation="system.notify:condition_v1", field="payload", equals=payload),
        installed_version_at_least="0.9.28",
    )
    notice = service.emit(
        NoticeKind.DOCTOR, "degraded", "", route="/doctor", command="hz system doctor", key="mixed", resolve_when=when
    )
    assert notice
    assert service.store.read()[0].resolve_when == when
    assert _resolve.holds(when, "999.0", lambda capability: True, lambda: {"sample: network"}) is None


def test_missing_provider_or_check_does_not_resolve_notice(service, make_kernel):
    from heinzel import Check, Status
    from heinzel.modules.system.doctor import module as doctor_module
    from heinzel.modules.system.notify._watch import doctor_problems

    provider = Module("sample", "Sample health provider.")
    reported = {"name": "other"}

    @provider.health
    def health() -> list[Check]:
        return [Check(name=reported["name"], status=Status.UP, detail="connected")]

    missing = make_kernel(system__doctor=doctor_module)
    service.problems = lambda: doctor_problems(missing)
    assert doctor(service, ["sample: network"])
    assert service.resolve() == 0
    present = make_kernel(system__doctor=doctor_module, sample=provider)
    service.problems = lambda: doctor_problems(present)
    assert service.resolve() == 0
    reported["name"] = "network"
    assert service.resolve() == 1


def test_plugin_only_condition_uses_legacy_compatible_envelope(service):
    class OldCondition(BaseModel, extra="forbid"):
        installed_version_at_least: str | None = None
        capability: CapabilityState | None = None

    when = ResolveWhen(plugins_installed=("example",))
    assert service.emit(NoticeKind.PLUGIN, "needed", "", route="/", command="hz", key="plugin-only", resolve_when=when)
    raw = json.loads(service.store.path.read_text())
    old = OldCondition.model_validate(raw[0]["resolve_when"])
    assert old.capability.operation == "system.notify:condition_v1"
    raw[0]["resolve_when"] = old.model_dump(mode="json")
    service.store.path.write_text(json.dumps(raw))
    assert service.store.read()[0].resolve_when == when
