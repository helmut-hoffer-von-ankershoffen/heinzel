"""A plugin that requires another gets it installed first; recommended ones are offered; repairs run once."""

import base64
import contextlib
import types

import pytest

from heinzel import HeinzelError, Kernel, Notifications, Progress, ResolveWhen
from heinzel.kernel import Installation
from heinzel.modules.system.notify import _resolve
from heinzel.modules.system.plugin._models import PluginCandidate, PluginChange, PluginInfo, PluginSettings
from heinzel.modules.system.plugin._render import render_change, render_repair
from heinzel.modules.system.plugin._service import PluginService
from heinzel.modules.system.plugin._sources import NEEDS, Metadata, github_project_at, plugin_needs

pytestmark = pytest.mark.unit

FIRST = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins"


def candidate(distribution, version="1.0.0", url=FIRST, *, first_party=True):
    return PluginCandidate(
        spec=distribution.removeprefix("heinzel-"),
        requirement=f"{distribution} @ git+{url}@abc",
        distribution=distribution,
        version=version,
        summary="",
        source="git",
        url=url,
        publisher="maintainers",
        first_party=first_party,
        private=False,
        revision="abc",
        installed_version=None,
        warnings=[],
    )


class Steps(Progress):
    """A head that shows progress: keeps each `phase` event as (id, state, detail)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, str | None]] = []

    def emitter(self):
        def emit(kind, data):
            if kind == "phase":
                self.events.append((data["id"], data["state"], data.get("detail")))

        return emit


class Notices(Notifications):
    def __init__(self) -> None:
        self.emitted: list[dict[str, object]] = []

    def emit(self, kind, title, body="", **fields):
        self.emitted.append({"kind": kind, "title": title, "body": body, **fields})


class World:
    """A fake catalog and installation: what each short name resolves to, what it declares, what is installed."""

    def __init__(self, make_kernel, tmp_path, monkeypatch, catalog, installed):
        kernel = Kernel(make_kernel().settings, [])
        self.progress = Steps()
        kernel.bind(Progress, self.progress)
        self.notices = Notices()
        kernel.bind(Notifications, self.notices)
        installation = Installation(Progress(), prefix=tmp_path, uv="uv")
        self.service = PluginService(kernel, installation, PluginSettings(github_token=None, catalog_sources=[]))
        self.catalog = catalog
        self.installed = dict(installed)
        self.order: list[str] = []
        self.fail: set[str] = set()
        self.reads = 0
        service = self.service
        monkeypatch.setattr(service, "_read_candidate", self.read)
        monkeypatch.setattr(service, "_versions_now", lambda: dict(self.installed))
        monkeypatch.setattr(service, "installed", self.listed)
        monkeypatch.setattr(service, "_mutation", contextlib.nullcontext)
        monkeypatch.setattr(service, "_install_candidate", self.install)

    def read(self, spec):
        self.reads += 1
        if spec not in self.catalog:
            msg = f"no plugin {spec}"
            raise HeinzelError(msg)
        found, requires, recommends = self.catalog[spec]
        return found, Metadata(name=found.distribution, requires=requires, recommends=recommends)

    def listed(self, *, fresh=False):
        del fresh
        return [
            PluginInfo(distribution=name, version=version, modules=[], name=name.removeprefix("heinzel-"))
            for name, version in self.installed.items()
        ]

    def install(self, found, spec, mutation, *, phases):
        del spec, mutation
        if found.distribution in self.fail:
            msg = "uv failed"
            raise HeinzelError(msg)
        if phases is not None:
            with phases.step("install", f"Installing {found.distribution}"):
                pass
        self.order.append(found.distribution)
        self.installed[found.distribution] = found.version
        return PluginChange(
            action="add",
            done=True,
            distribution=found.distribution,
            candidate=found,
            plugins=self.listed(),
            restart_required=True,
            next=[],
        )


ACME = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-acme"


def acme_world(make_kernel, tmp_path, monkeypatch, installed=()):
    catalog = {
        "acme": (
            candidate("heinzel-acme", "0.3.0", ACME),
            ("heinzel-github", "heinzel-atlassian>=0.2"),
            ("heinzel-google", "heinzel-zoom"),
        ),
        "github": (candidate("heinzel-github", "0.5.0"), ("heinzel-acme",), ()),  # a cycle back to acme
        "atlassian": (candidate("heinzel-atlassian", "0.4.0"), ("heinzel-github",), ()),
        "google": (candidate("heinzel-google", "0.9.2"), (), ()),
    }
    return World(make_kernel, tmp_path, monkeypatch, catalog, dict(installed))


def test_a_pyproject_declares_required_and_recommended_plugins_without_sources():
    document = {
        "tool": {
            "heinzel": {
                "plugin": {
                    "requires": ["core:plugin-api@1"],
                    "requires-plugins": [
                        "heinzel-github",
                        "Heinzel_Atlassian>=0.2",
                        "heinzel-x @ https://evil.example/x.whl",
                    ],
                    "recommends-plugins": [
                        "heinzel-google",
                        "heinzel-zoom[extra]",
                        "requests",
                        "heinzel",
                        "not valid !",
                    ],
                }
            }
        }
    }
    assert plugin_needs(document) == {
        "requires": ("heinzel-github", "heinzel-atlassian>=0.2"),
        "recommends": ("heinzel-google",),
    }
    assert plugin_needs({"tool": {"heinzel": {"plugin": "no table"}}}) == {"requires": (), "recommends": ()}


def test_github_metadata_carries_what_the_plugin_needs():
    pyproject = b'[project]\nname = "heinzel-acme"\n[tool.heinzel.plugin]\nrequires-plugins = ["heinzel-github"]\n'
    answer = {"type": "file", "content": base64.b64encode(pyproject).decode()}
    project = github_project_at(lambda _path: answer, "repos/o/r", "abc", None)
    assert project["name"] == "heinzel-acme"
    assert project[NEEDS] == {"requires": ("heinzel-github",), "recommends": ()}


def test_the_preview_says_up_front_what_comes_with_it_in_dependency_order(make_kernel, tmp_path, monkeypatch):
    world = acme_world(make_kernel, tmp_path, monkeypatch, {"heinzel-google": "0.9.2"})
    preview = world.service.candidate("acme")
    assert [(n.distribution, n.state, n.needed_by) for n in preview.needs] == [
        ("heinzel-github", "install", "heinzel-acme"),
        ("heinzel-atlassian", "install", "heinzel-acme"),
        ("heinzel-zoom", "offered", "heinzel-acme"),
    ]
    assert preview.also == (
        "heinzel-acme also needs heinzel-github and heinzel-atlassian; installing them too. "
        "It recommends heinzel-zoom, not installed: hz system plugin add zoom."
    )
    assert world.order == []  # a preview installs nothing
    rendered = render_change(
        PluginChange(
            action="add",
            done=False,
            distribution="heinzel-acme",
            candidate=preview,
            plugins=[],
            restart_required=False,
            next=[],
        )
    )
    assert rendered is not None


def test_a_confirmed_add_installs_what_it_requires_first_with_one_step_each(make_kernel, tmp_path, monkeypatch):
    world = acme_world(make_kernel, tmp_path, monkeypatch, {"heinzel-atlassian": "0.1.0"})
    change = world.service._add_confirmed(world.service.candidate("acme"), "acme")
    assert world.order == ["heinzel-github", "heinzel-atlassian", "heinzel-acme"]
    assert [(n.distribution, n.state, n.version) for n in change.needs if n.required] == [
        ("heinzel-github", "installed", "0.5.0"),
        ("heinzel-atlassian", "updated", "0.4.0"),  # 0.1.0 was too old for >=0.2
    ]
    assert world.progress.events[:4] == [
        ("requires:heinzel-github", "pending", None),
        ("requires:heinzel-atlassian", "pending", None),
        ("install", "pending", None),
        ("verify", "pending", None),
    ]
    finished = [e[0] for e in world.progress.events[4:] if e[1] == "done"]
    assert finished == ["requires:heinzel-github", "requires:heinzel-atlassian", "install"]


def test_an_add_again_is_idempotent_when_what_it_requires_is_there(make_kernel, tmp_path, monkeypatch):
    world = acme_world(make_kernel, tmp_path, monkeypatch, {"heinzel-github": "0.5.0", "heinzel-atlassian": "0.4.0"})
    preview = world.service.candidate("acme")
    assert {n.distribution: n.state for n in preview.needs if n.required} == {
        "heinzel-github": "present",
        "heinzel-atlassian": "present",
    }
    assert preview.also.startswith("It recommends heinzel-google and heinzel-zoom")
    world.service._add_confirmed(preview, "acme")
    assert world.order == ["heinzel-acme"]


def test_a_failed_requirement_stops_the_add_and_says_what_was_not_reached(make_kernel, tmp_path, monkeypatch):
    world = acme_world(make_kernel, tmp_path, monkeypatch)
    world.fail = {"heinzel-github"}
    preview = world.service.candidate("acme")
    with pytest.raises(HeinzelError, match="heinzel-github, which heinzel-acme needs, couldn't be installed"):
        world.service._add_confirmed(preview, "acme")
    assert world.order == []
    states = {e[0]: e[1] for e in world.progress.events}
    assert states["requires:heinzel-github"] == "failed"
    assert states["requires:heinzel-atlassian"] == "skipped"
    assert states["install"] == "pending"


def test_a_requirement_from_another_publisher_is_refused(make_kernel, tmp_path, monkeypatch):
    world = acme_world(make_kernel, tmp_path, monkeypatch)
    world.catalog["github"] = (
        candidate("heinzel-github", url="https://github.com/someone-else/heinzel-github", first_party=False),
        (),
        (),
    )
    with pytest.raises(HeinzelError, match="neither first-party nor from the same publisher"):
        world.service.candidate("acme")


def test_a_requirement_the_catalog_lacks_names_it(make_kernel, tmp_path, monkeypatch):
    world = acme_world(make_kernel, tmp_path, monkeypatch)
    del world.catalog["atlassian"]
    with pytest.raises(HeinzelError, match=r"heinzel-acme needs heinzel-atlassian>=0\.2, which can't be read"):
        world.service.candidate("acme")


def test_repair_installs_what_an_installed_plugin_lacks_once_and_never_signs_in(make_kernel, tmp_path, monkeypatch):
    # The MacBook Air case: acme installed, github and atlassian missing.
    world = acme_world(make_kernel, tmp_path, monkeypatch, {"heinzel-acme": "0.3.0", "heinzel-google": "0.9.2"})
    repair = world.service.repair(automatic=True)
    assert world.order == ["heinzel-github", "heinzel-atlassian"]
    assert repair.restart_required
    assert [(n.distribution, n.state) for n in repair.needs] == [
        ("heinzel-github", "installed"),
        ("heinzel-atlassian", "installed"),
    ]
    assert repair.message.startswith("Installed heinzel-github and heinzel-atlassian, which heinzel-acme needs")
    assert render_repair(repair) is not None
    assert [n["title"] for n in world.notices.emitted] == [
        "Installed heinzel-github, heinzel-atlassian, which heinzel-acme needs"
    ]
    reads = world.reads
    again = world.service.repair(automatic=True)
    assert again.needs == []
    assert not again.restart_required
    assert world.order == ["heinzel-github", "heinzel-atlassian"]
    # What each installed version requires is remembered: acme isn't read again (github and atlassian are, once).
    assert world.reads - reads == 2


def test_a_failed_automatic_repair_is_tried_once_and_its_notice_resolves_once_installed(
    make_kernel, tmp_path, monkeypatch
):
    world = acme_world(make_kernel, tmp_path, monkeypatch, {"heinzel-acme": "0.3.0"})
    world.fail = {"heinzel-github"}
    first = world.service.repair(automatic=True)
    assert {n.state for n in first.needs} == {"failed"}
    notice = world.notices.emitted[-1]
    assert notice["command"] == "hz system plugin repair"
    assert notice["resolve_when"] == ResolveWhen(plugins_installed=("heinzel-atlassian", "heinzel-github"))
    assert world.service.repair(automatic=True).needs == []  # not retried every half hour
    world.fail = set()
    by_hand = world.service.repair()
    assert [n.state for n in by_hand.needs] == ["installed", "installed"]


def test_a_plugins_installed_condition_holds_once_each_is_on_disk(monkeypatch):
    present = {"heinzel-github"}
    monkeypatch.setattr(_resolve, "plugin_installed", lambda name: name in present)
    when = ResolveWhen(plugins_installed=("heinzel-github", "heinzel-atlassian"))
    assert _resolve.holds(when, "1.0.0", None) is None
    present.add("heinzel-atlassian")
    assert _resolve.holds(when, "1.0.0", None) == "heinzel-github, heinzel-atlassian are installed"


def test_the_auto_updater_repairs_when_nothing_else_is_due_and_restarts_to_load(make_kernel, tmp_path):
    from heinzel.heads.web import _auto_update

    restarts: list[bool] = []
    calls: list[dict[str, object]] = []
    operation = types.SimpleNamespace(name="repair", func=object())
    kernel = types.SimpleNamespace(
        lets=lambda _kind: [(types.SimpleNamespace(qualname="system.plugin"), operation)],
        available=lambda _func: True,
        resolve=lambda _type: types.SimpleNamespace(local=lambda _who: contextlib.nullcontext()),
        invoke=lambda _op, **kwargs: (
            calls.append(kwargs) or types.SimpleNamespace(needs=[1], restart_required=True, message="Installed")
        ),
    )
    updater = types.SimpleNamespace(_kernel=kernel, _restart=lambda: restarts.append(True))
    _auto_update.AutoUpdater._repair_plugins(updater)
    assert calls == [{"automatic": True}]
    assert restarts == [True]


def events(caplog):
    return [
        {
            k: getattr(r, k, None)
            for k in ("event", "operation", "step", "plugin", "version", "dependency_of", "outcome")
        }
        | {"timed": isinstance(getattr(r, "duration_ms", None), int), "error": getattr(r, "error", None)}
        for r in caplog.records
        if getattr(r, "event", "").startswith("plugin.requires.")
    ]


def test_each_required_plugin_logs_a_start_and_an_outcome(make_kernel, tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO", logger="heinzel.modules.system.plugin._service")
    world = acme_world(make_kernel, tmp_path, monkeypatch, {"heinzel-atlassian": "0.4.0"})
    world.service._add_confirmed(world.service.candidate("acme"), "acme")
    common = {"operation": "add", "step": "requires:heinzel-github", "plugin": "heinzel-github"}
    assert events(caplog) == [
        {
            "event": "plugin.requires.start",
            **common,
            "version": "0.5.0",
            "dependency_of": "heinzel-acme",
            "outcome": None,
            "timed": False,
            "error": None,
        },
        {
            "event": "plugin.requires.outcome",
            **common,
            "version": "0.5.0",
            "dependency_of": "heinzel-acme",
            "outcome": "installed",
            "timed": True,
            "error": None,
        },
    ]


def test_a_failed_required_plugin_logs_its_outcome_and_error(make_kernel, tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO", logger="heinzel.modules.system.plugin._service")
    world = acme_world(make_kernel, tmp_path, monkeypatch, {"heinzel-acme": "0.3.0"})
    world.fail = {"heinzel-github"}
    world.service.repair(automatic=True)
    outcomes = [(e["plugin"], e["operation"], e["outcome"], e["error"]) for e in events(caplog) if e["timed"]]
    assert outcomes == [
        ("heinzel-github", "repair", "failed", "uv failed"),
        ("heinzel-atlassian", "repair", "not-reached", None),
    ]
    repaired = [r for r in caplog.records if getattr(r, "event", "") == "plugin.repair.outcome"]
    assert [(r.outcome, r.failed, r.levelname) for r in repaired] == [
        ("failed", ["heinzel-github", "heinzel-atlassian"], "WARNING")
    ]
