"""A plugin that requires another gets it installed first; recommended ones are offered; repairs run once."""

import base64
import contextlib
import json
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
        self.binds: list[tuple[str, ...]] = []
        self.bindings: list[dict[str, object]] = []
        self.bind_fails = False
        monkeypatch.setattr(installation, "hz", self.hz)

    def hz(self, *args):
        """The fresh `hz` the bind step runs: answers with the bindings the test set."""
        assert args[:3] == ("system", "plugin", "bind")
        self.binds.append(args)
        if self.bind_fails:
            msg = "`hz system plugin bind` failed after the change:\nboom"
            raise HeinzelError(msg)
        return json.dumps({"bindings": self.bindings, "message": ""})

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
    preview = world.service.candidate("acme", with_recommended=False)
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
    change = world.service._add_confirmed(world.service.candidate("acme", with_recommended=False), "acme")
    assert world.order == ["heinzel-github", "heinzel-atlassian", "heinzel-acme"]
    assert [(n.distribution, n.state, n.version) for n in change.needs if n.required] == [
        ("heinzel-github", "installed", "0.5.0"),
        ("heinzel-atlassian", "updated", "0.4.0"),  # 0.1.0 was too old for >=0.2
    ]
    assert world.progress.events[:5] == [
        ("requires:heinzel-github", "pending", None),
        ("requires:heinzel-atlassian", "pending", None),
        ("install", "pending", None),
        ("verify", "pending", None),
        ("bind", "pending", None),
    ]
    finished = [e[0] for e in world.progress.events[5:] if e[1] in {"done", "skipped"}]
    assert finished == ["requires:heinzel-github", "requires:heinzel-atlassian", "install", "bind"]


def test_an_add_again_is_idempotent_when_what_it_requires_is_there(make_kernel, tmp_path, monkeypatch):
    world = acme_world(make_kernel, tmp_path, monkeypatch, {"heinzel-github": "0.5.0", "heinzel-atlassian": "0.4.0"})
    preview = world.service.candidate("acme", with_recommended=False)
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


FIELDS = ("event", "phase", "operation", "step", "plugin", "version", "dependency_of", "outcome", "state", "error")


def _plain(renderable):
    from rich.console import Console

    console = Console(width=200, record=True, color_system=None)
    console.print(renderable)
    return console.export_text()


def events(caplog, name):
    return [
        {k: getattr(r, k, None) for k in FIELDS} | {"timed": isinstance(getattr(r, "duration_ms", None), int)}
        for r in caplog.records
        if getattr(r, "event", None) == name
    ]


def test_each_required_plugin_logs_a_start_and_an_outcome(make_kernel, tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO", logger="heinzel.plugin")
    world = acme_world(make_kernel, tmp_path, monkeypatch, {"heinzel-atlassian": "0.4.0"})
    world.service._add_confirmed(world.service.candidate("acme"), "acme")
    common = {
        "event": "plugin.add.requires",
        "operation": "add",
        "step": "requires:heinzel-github",
        "plugin": "heinzel-github",
        "version": "0.5.0",
        "dependency_of": "heinzel-acme",
        "error": None,
    }
    assert events(caplog, "plugin.add.requires") == [
        {**common, "phase": "start", "outcome": None, "state": None, "timed": False},
        {**common, "phase": "end", "outcome": "ok", "state": "installed", "timed": True},
    ]


def test_a_failed_required_plugin_logs_its_outcome_and_error(make_kernel, tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO", logger="heinzel.plugin")
    world = acme_world(make_kernel, tmp_path, monkeypatch, {"heinzel-acme": "0.3.0"})
    world.fail = {"heinzel-github"}
    world.service.repair(automatic=True)
    ends = [e for e in events(caplog, "plugin.repair.requires") if e["phase"] == "end"]
    assert [(e["plugin"], e["outcome"], e["state"], e["error"]) for e in ends] == [
        ("heinzel-github", "error", "failed", "uv failed"),
        ("heinzel-atlassian", "skipped", "not-reached", None),
    ]
    [run] = [r for r in caplog.records if getattr(r, "event", None) == "plugin.repair.run" and r.phase == "end"]
    assert (run.outcome, run.failed) == ("error", ["heinzel-github", "heinzel-atlassian"])


def test_recommended_plugins_come_with_the_add_by_default_and_one_the_catalog_lacks_is_offered(
    make_kernel, tmp_path, monkeypatch
):
    world = acme_world(make_kernel, tmp_path, monkeypatch)
    preview = world.service.candidate("acme")
    assert [(n.distribution, n.state, n.required) for n in preview.needs] == [
        ("heinzel-github", "install", True),
        ("heinzel-atlassian", "install", True),
        ("heinzel-google", "install", False),
        ("heinzel-zoom", "offered", False),  # not in the catalog
    ]
    assert "It recommends heinzel-google; installing it too." in preview.also
    change = world.service._add_confirmed(preview, "acme")
    assert world.order == ["heinzel-github", "heinzel-atlassian", "heinzel-google", "heinzel-acme"]
    assert {n.distribution: n.state for n in change.needs}["heinzel-google"] == "installed"
    assert ("recommends:heinzel-google", "done", None) in world.progress.events


def test_without_recommended_they_are_only_offered(make_kernel, tmp_path, monkeypatch):
    world = acme_world(make_kernel, tmp_path, monkeypatch)
    preview = world.service.candidate("acme", with_recommended=False)
    assert {n.distribution: n.state for n in preview.needs if not n.required} == {
        "heinzel-google": "offered",
        "heinzel-zoom": "offered",
    }


def test_a_recommended_plugin_that_fails_is_said_and_the_add_goes_on(make_kernel, tmp_path, monkeypatch):
    world = acme_world(make_kernel, tmp_path, monkeypatch)
    world.fail = {"heinzel-google"}
    change = world.service._add_confirmed(world.service.candidate("acme"), "acme")
    assert world.order == ["heinzel-github", "heinzel-atlassian", "heinzel-acme"]
    google = next(n for n in change.needs if n.distribution == "heinzel-google")
    assert (google.state, google.detail) == ("failed", "uv failed")


BINDINGS = [
    {"capability": "code-host", "adapter": "github", "state": "written", "reason": "", "by": "acme"},
    {"capability": "tracker", "adapter": "atlassian", "state": "kept", "reason": "yours: github", "by": "acme"},
]


def test_the_add_sets_the_recommended_bindings_in_a_fresh_hz_for_what_it_installed(make_kernel, tmp_path, monkeypatch):
    world = acme_world(make_kernel, tmp_path, monkeypatch)
    world.bindings = BINDINGS
    change = world.service._add_confirmed(world.service.candidate("acme"), "acme", operation_id="op-7")
    [args] = world.binds
    plugins = [args[i + 1] for i, a in enumerate(args) if a == "--plugin"]
    assert plugins == ["heinzel-acme", "heinzel-github", "heinzel-atlassian", "heinzel-google"]
    assert args[-2:] == ("--operation-id", "op-7")
    assert "--decline" not in args
    assert "--automatic" in args  # an install binds only for layer plugins, and never what the person undid
    assert [(b.capability, b.state) for b in change.bindings] == [("code-host", "written"), ("tracker", "kept")]
    assert ("bind", "done", "code-host → github; kept tracker (yours)") in world.progress.events
    text = _plain(render_change(change))
    assert "code-host → github, as acme recommends" in text
    assert "Undo with: hz system plugin unbind acme" in text


def test_no_bind_writes_nothing_and_remembers_it(make_kernel, tmp_path, monkeypatch):
    world = acme_world(make_kernel, tmp_path, monkeypatch)
    world.service._add_confirmed(world.service.candidate("acme"), "acme", bind=False)
    [args] = world.binds
    assert "--decline" in args
    assert ("bind", "skipped", "Not asked to: no binding set") in world.progress.events


def test_a_bind_that_cannot_run_is_a_warning_and_a_next_step_not_a_failed_install(
    make_kernel, tmp_path, monkeypatch, caplog
):
    caplog.set_level("INFO", logger="heinzel.plugin")
    world = acme_world(make_kernel, tmp_path, monkeypatch)
    world.bind_fails = True
    change = world.service._add_confirmed(world.service.candidate("acme"), "acme")
    assert change.done
    assert change.bindings == []
    assert change.next[-1] == "hz system plugin bind --plugin heinzel-acme"
    assert any(e[0] == "bind" and e[1] == "warn" for e in world.progress.events)
    [end] = [e for e in events(caplog, "plugin.add.bind") if e["phase"] == "end"]
    assert end["outcome"] == "error"
    assert "failed after the change" in end["error"]


def test_bind_success_logs_its_fields(make_kernel, tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO", logger="heinzel.plugin")
    world = acme_world(make_kernel, tmp_path, monkeypatch)
    world.bindings = BINDINGS
    world.service._add_confirmed(world.service.candidate("acme"), "acme")
    [end] = [r for r in caplog.records if getattr(r, "event", None) == "plugin.add.bind" and r.phase == "end"]
    assert (end.outcome, end.written, end.kept, end.step) == ("ok", ["code-host"], ["tracker"], "bind")
    assert isinstance(end.duration_ms, int)


def test_repair_sets_bindings_of_playbooks_installed_before_once_per_version(make_kernel, tmp_path, monkeypatch):
    from heinzel import Module

    world = acme_world(make_kernel, tmp_path, monkeypatch, {"heinzel-acme": "0.3.0", "heinzel-github": "0.5.0"})
    company = Module("acme", "A company.")
    company.playbook("Tools.", defaults={"capabilities.code_host": "github"}, setup=False)
    kernel = make_kernel(acme=company)
    kernel.bind(Progress, world.progress)
    world.service._kernel = kernel
    monkeypatch.setattr(world.service, "_read_candidate", world.read)
    world.installed["heinzel-atlassian"] = "0.4.0"
    world.bindings = BINDINGS
    repair = world.service.repair(automatic=True)
    assert [(b.capability, b.state) for b in repair.bindings] == [("code-host", "written")]
    assert "Set code-host → github." in repair.message
    assert "--once" in world.binds[0]
    world.service.repair(automatic=True)
    assert len(world.binds) == 1  # the updater asks once per version
