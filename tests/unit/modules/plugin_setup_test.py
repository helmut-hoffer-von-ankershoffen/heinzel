"""A company playbook's recommended bindings are set at install, a person's binding always wins, and undo works."""

import json
import logging
from pathlib import Path

import pytest

from heinzel import Check, ConfigEditor, HeinzelError, Login, Module, Progress, Status
from heinzel.kernel import CAPABILITIES, CapabilityView, Config
from heinzel.modules.system.plugin._setup import STATE_FILE, SetupService

pytestmark = pytest.mark.unit


class Editor(ConfigEditor):
    """Writes into a dict instead of a file, so a test sees exactly what setup wrote and unset."""

    def __init__(self) -> None:
        self.written: dict[str, object] = {}
        self.unset_keys: list[str] = []
        self.refuse: set[str] = set()

    def write(self, key, value, scope="user"):
        if key in self.refuse:
            msg = f"{key} refused"
            raise HeinzelError(msg)
        self.written[key] = value
        return Path("/config.toml")

    def unset(self, key, scope="user"):
        self.unset_keys.append(key)
        self.written.pop(key, None)
        return Path("/config.toml")


class Steps(Progress):
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def emitter(self):
        def emit(kind, data):
            if kind == "phase":
                self.events.append((data["id"], data["state"]))

        return emit


class Adapter:
    """Registers as an adapter; never called here."""


def connector(name, *capabilities, checks=()):
    module = Module(name, f"The {name} connector.")
    for capability in capabilities:
        methods = {method: lambda *_a, **_k: None for method in CAPABILITIES[capability].methods}
        module.provides(capability, modes={"read"}, name=name)(type(f"{name}_{capability}", (Adapter,), methods))

    @module.health
    def health() -> list[Check]:
        """Its checks."""
        return list(checks) or [Check(name="cli", status=Status.UP, detail="fine")]

    return module


def company(name="acme", defaults=None, **extra):
    module = Module(name, "A made-up company.")
    module.playbook(
        "How the company reaches its tools.",
        defaults=defaults
        or {
            "capabilities.tracker": "jira",
            "capabilities.documents": "drive",
            "capabilities.mail": "drive",
            "capabilities.meetings": "zoomy",
        },
        setup=False,
        **extra,
    )
    return module


SIGNED_OUT = Check(
    name="cli",
    status=Status.DEGRADED,
    detail="not signed in",
    fix="jira login",
    login=Login(signed_in=False, command="jira login"),
)


def world(make_kernel, bindings=None, **modules):
    modules = {
        "acme": company(),
        "jira": connector("jira", "tracker", checks=[SIGNED_OUT]),
        "gh": connector("gh", "tracker", "code-host"),
        "drive": connector("drive", "documents", "mail"),
        **modules,
    }
    kernel = make_kernel(config=Config(files=(), data={"capabilities": bindings or {}}), **modules)
    steps = Steps()
    kernel.bind(Progress, steps)
    editor = Editor()
    service = SetupService(kernel, kernel.resolve(CapabilityView), editor)
    return service, editor, steps


def states(result):
    return {b.capability: (b.state, b.adapter, b.reason) for b in result.bindings}


def test_installed_connectors_are_bound_as_recommended_and_a_missing_one_is_skipped(make_kernel):
    service, editor, _ = world(make_kernel)
    result = service.bind(plugins=["heinzel-test"], operation_id="op-1")
    assert states(result) == {
        "tracker": ("written", "jira", ""),
        "documents": ("written", "drive", ""),
        "mail": ("written", "drive", ""),
        "meetings": ("skipped", "zoomy", "not installed"),
    }
    assert editor.written == {
        "capabilities.tracker": "jira",
        "capabilities.documents": "drive",
        "capabilities.mail": "drive",
    }
    assert result.message == "Set 3 connector bindings; skipped 1."
    assert service.set_by() == dict.fromkeys(editor.written, "acme")


def test_a_binding_the_person_set_always_wins(make_kernel):
    service, editor, _ = world(make_kernel, bindings={"tracker": "gh", "mail": "none"})
    result = service.bind(module="acme")
    assert states(result)["tracker"] == ("kept", "jira", "yours: gh")
    assert states(result)["mail"] == ("kept", "drive", "yours: none")
    assert "capabilities.tracker" not in editor.written
    assert "capabilities.mail" not in editor.written
    assert editor.written == {"capabilities.documents": "drive"}


def test_a_binding_already_set_as_recommended_is_kept_without_writing(make_kernel):
    service, editor, _ = world(make_kernel, bindings={"tracker": "jira"})
    assert states(service.bind(module="acme"))["tracker"] == ("kept", "jira", "already set")
    assert "capabilities.tracker" not in editor.written


def test_the_sole_adapter_is_still_written_so_a_second_one_later_does_not_unbind_it(make_kernel):
    service, editor, _ = world(make_kernel, acme=company(defaults={"capabilities.code_host": "gh"}))
    assert states(service.bind(module="acme"))["code-host"] == ("written", "gh", "")
    assert editor.written == {"capabilities.code_host": "gh"}


def test_two_playbooks_that_disagree_bind_nothing(make_kernel):
    other = company("globex", defaults={"capabilities.tracker": "gh"})
    service, editor, _ = world(make_kernel, globex=other)
    result = service.bind()
    tracker = states(result)["tracker"]
    assert tracker[:2] == ("conflict", None)
    assert "acme recommends jira" in tracker[2]
    assert "globex recommends gh" in tracker[2]
    assert "capabilities.tracker" not in editor.written


def test_a_dry_run_or_a_decline_writes_nothing(make_kernel):
    service, editor, _ = world(make_kernel)
    assert states(service.bind(module="acme", dry_run=True))["tracker"][0] == "written"
    assert service.bind(module="acme", dry_run=True).message.startswith("Would set 3")
    assert {s for s, _, _ in states(service.bind(module="acme", decline=True)).values()} == {"skipped"}
    assert editor.written == {}


def test_once_considers_each_playbook_once_per_version_and_a_decline_counts(make_kernel):
    service, editor, _ = world(make_kernel)
    service.bind(decline=True)
    assert service.bind(once=True).bindings == []
    assert editor.written == {}
    fresh, editor, _ = world(make_kernel)
    (Path(fresh._path)).unlink()
    assert len(fresh.bind(once=True).bindings) == 4
    assert fresh.bind(once=True).bindings == []


def test_a_refused_write_is_skipped_and_the_others_go_on(make_kernel):
    service, editor, _ = world(make_kernel)
    editor.refuse.add("capabilities.tracker")
    result = states(service.bind(module="acme"))
    assert result["tracker"][0] == "skipped"
    assert result["tracker"][2].startswith("not saved")
    assert result["documents"][0] == "written"


def test_undo_removes_what_setup_wrote_and_keeps_what_the_person_changed_since(make_kernel, tmp_path):
    service, _, _ = world(make_kernel)
    service.bind(module="acme", operation_id="op-1")
    recorded = json.loads(service._path.read_text())
    assert recorded["written"]["capabilities.tracker"]["operation_id"] == "op-1"
    # A later process sees the configuration setup wrote, except mail, which the person changed to gh.
    later, later_editor, _ = world(
        make_kernel, bindings={"tracker": "jira", "documents": "drive", "mail": "gh"}, gh=connector("gh", "mail")
    )
    result = later.unbind("acme")
    assert {b.capability: b.state for b in result.bindings} == {
        "tracker": "removed",
        "documents": "removed",
        "mail": "kept",
    }
    assert sorted(later_editor.unset_keys) == ["capabilities.documents", "capabilities.tracker"]
    assert later.set_by() == {}
    assert result.message == "Removed 2 bindings acme set."


def test_each_binding_logs_a_start_and_an_outcome_also_for_a_conflict(make_kernel, caplog):
    other = company("globex", defaults={"capabilities.documents": "gh"})
    service, _, _ = world(make_kernel, globex=other)
    with caplog.at_level(logging.INFO, logger="heinzel.plugin"):
        service.bind()
    events = [r for r in caplog.records if getattr(r, "event", "") == "plugin.bind.binding"]
    ends = {r.capability: r for r in events if r.phase == "end"}
    assert len([r for r in events if r.phase == "start"]) == len(ends) == 4
    assert ends["tracker"].outcome == "ok"
    assert ends["tracker"].state == "written"
    assert ends["tracker"].plugin == "acme"
    assert isinstance(ends["tracker"].duration_ms, int)
    assert ends["documents"].state == "conflict"
    assert ends["documents"].outcome == "skipped"
    run = [r for r in caplog.records if getattr(r, "event", "") == "plugin.bind.run" and r.phase == "end"]
    assert run[0].written == ["tracker", "mail"]
    assert run[0].conflicts == ["documents"]


def test_an_unreadable_state_file_starts_over(make_kernel):
    service, _, _ = world(make_kernel)
    service._path.parent.mkdir(parents=True, exist_ok=True)
    service._path.write_text("{not json")
    assert len(service.bind(module="acme").bindings) == 4
    assert service._path.name == STATE_FILE


def test_the_checklist_has_one_row_per_connector_with_one_action(make_kernel):
    attention = Check(name="site", status=Status.DEGRADED, detail="site_url is not set", fix="drive site")
    service, _, _ = world(make_kernel, drive=connector("drive", "documents", "mail", checks=[attention]))
    before = {row.connector: row for row in service.checklist("acme").rows}
    assert before["jira"].state == "needs-attention"
    assert before["jira"].action.kind == "bind"
    assert not before["jira"].owner_only
    assert before["zoomy"].state == "not-installed"
    assert before["zoomy"].action.kind == "install"
    after = service.checklist("acme", bound_now={"tracker": "jira", "documents": "drive", "mail": "drive"})
    rows = {row.connector: row for row in after.rows}
    assert rows["jira"].state == "not-connected"
    assert rows["jira"].action.kind == "sign-in"
    assert rows["jira"].action.command == "jira login"
    assert rows["jira"].owner_only
    assert rows["drive"].state == "needs-attention"
    assert rows["drive"].line == "site_url is not set"
    assert rows["drive"].owner_only
    assert not after.done
    assert all(len(row.line) <= 60 for row in after.rows)


def test_rows_go_green_once_signed_in_and_a_binding_of_their_own_is_theirs(make_kernel):
    signed_in = Check(name="cli", status=Status.UP, detail="ok", login=Login(signed_in=True, account="sam"))
    service, _, _ = world(
        make_kernel,
        bindings={"tracker": "gh", "documents": "drive", "mail": "drive", "meetings": "none"},
        acme=company(defaults={"capabilities.tracker": "jira", "capabilities.documents": "drive"}),
        drive=connector("drive", "documents", "mail", checks=[signed_in]),
    )
    rows = {row.connector: row for row in service.checklist("acme").rows}
    assert rows["drive"].state == "connected"
    assert rows["drive"].line == "Signed in as sam"
    # The person bound tracker to gh: jira serves nothing it was asked to, and nothing is to do about it.
    assert rows["jira"].capabilities == []


def test_setup_run_binds_then_lists_what_is_left_with_one_step_each(make_kernel):
    service, editor, steps = world(make_kernel)
    result = service.run("acme")
    assert [(s.name, s.state) for s in result.steps] == [
        ("bind", "done"),
        ("settings", "skipped"),
        ("templates", "skipped"),
        ("check", "done"),
    ]
    assert editor.written["capabilities.tracker"] == "jira"
    assert {row.connector: row.state for row in result.checklist.rows}["jira"] == "not-connected"
    assert ("bind", "done") in steps.events
    with pytest.raises(HeinzelError, match="doesn't recommend"):
        service.run("acme", {"acme.region": "x"})
    with pytest.raises(HeinzelError, match="No installed module"):
        service.run("nobody")


def test_answers_are_key_value_pairs_read_as_json_or_text():
    from heinzel.modules.system.plugin import parse_answers

    assert parse_answers(["acme.region=lisbon", "acme.days=3", 'acme.tags=["a"]']) == {
        "acme.region": "lisbon",
        "acme.days": 3,
        "acme.tags": ["a"],
    }
    with pytest.raises(HeinzelError, match="key=value"):
        parse_answers(["nokey"])
