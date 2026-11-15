"""How the CLI presents plugin results, and how the plugin operations hand their work to the services."""

import io
from pathlib import Path

import pytest
from rich.console import Console

from heinzel import HeinzelError
from heinzel.kernel import CurrentUser, ScriptedPrompter
from heinzel.modules.system import plugin as plugin_module
from heinzel.modules.system.plugin import _render
from heinzel.modules.system.plugin._models import (
    AvailablePlugin,
    PluginBinding,
    PluginCandidate,
    PluginChange,
    PluginInfo,
    PluginLayer,
    PluginNeed,
    PluginRepair,
    PluginSearch,
    PluginUnpin,
)
from heinzel.modules.system.plugin._setup_models import (
    Checklist,
    ChecklistRow,
    RowAction,
    SetupBind,
    SetupResult,
    SetupStep,
)

pytestmark = pytest.mark.unit


def text(renderable, width=200):
    console = Console(file=io.StringIO(), width=width, force_terminal=False, color_system=None)
    console.print(renderable)
    return console.file.getvalue()


def candidate(**over):
    base = {
        "spec": "crm",
        "requirement": "heinzel-crm @ git+https://github.com/acme/heinzel-crm@abc",
        "distribution": "heinzel-crm",
        "version": "1.2.0",
        "summary": "A CRM.",
        "source": "git",
        "url": "https://github.com/acme/heinzel-crm",
        "publisher": "acme",
        "first_party": False,
        "private": None,
        "revision": "abc",
        "installed_version": None,
        "warnings": [],
    }
    return PluginCandidate(**(base | over))


def info(**over):
    base = {"distribution": "heinzel-crm", "version": "1.2.0", "modules": ["crm"], "name": "crm"}
    return PluginInfo(**(base | over))


def change(**over):
    base = {
        "action": "add",
        "done": False,
        "distribution": "heinzel-crm",
        "candidate": candidate(),
        "plugins": [],
        "restart_required": True,
        "next": ["hz system plugin add crm --confirm"],
    }
    return PluginChange(**(base | over))


def need(state, **over):
    base = {
        "distribution": "heinzel-github",
        "requirement": "heinzel-github",
        "required": True,
        "needed_by": "heinzel-crm",
        "state": state,
        "install": "github",
        "version": "2.0.0",
        "detail": "sign in first.",
    }
    return PluginNeed(**(base | over))


def binding(state, **over):
    base = {
        "capability": "tracker",
        "adapter": "github",
        "state": state,
        "reason": "yours: jira",
        "by": "acme",
    }
    return PluginBinding(**(base | over))


def test_unpin_shows_message_and_the_exact_command():
    out = text(
        _render.render_unpin(
            PluginUnpin(
                distribution="heinzel-crm",
                version="1.2.0",
                done=False,
                confirmation="abc",
                message="Keeps 1.2.0.",
                next=["hz system plugin unpin heinzel-crm --confirm abc"],
            )
        )
    )
    assert "Keeps 1.2.0." in out
    assert "hz system plugin unpin heinzel-crm --confirm abc" in out


def test_search_marks_first_party_community_installed_and_stars():
    def available(name, **over):
        base = {
            "name": f"heinzel-{name}",
            "short_name": name,
            "description": f"The {name} plugin.",
            "source": "first-party",
            "community": False,
            "install_spec": name,
            "url": "https://example.com",
            "repository_url": None,
            "stars": None,
            "installed": False,
        }
        return AvailablePlugin(**(base | over))

    out = text(
        _render.render_search(
            PluginSearch(
                plugins=[
                    available("clock"),
                    available("crm", community=True, source="github", stars=7, install_spec="git+https://x/crm"),
                    available("done", installed=True),
                ],
                warnings=["GitHub is offline."],
            )
        )
    )
    assert "first-party" in out
    assert "community, GitHub" in out
    assert "git+https://x/crm" in out
    assert "(installed)" in out
    assert " 7 " in out
    assert "Warning: GitHub is offline." in out
    assert _render.RIGHTS.split(".")[0] in out


def test_list_without_plugins_points_to_search():
    assert "hz system plugin search" in text(_render.render_list([]))


def test_list_shows_state_origin_and_what_each_brings():
    plugins = [
        info(
            distribution="heinzel-a",
            first_party=True,
            capabilities=["github:tracker"],
            layers=[PluginLayer(module="acme", parent=None, site=None)],
            themes=["acme.dark"],
        ),
        info(distribution="heinzel-b", enabled=False, source_kind="git", source_url="https://github.com/x/b"),
        info(distribution="heinzel-c", error="boom", source_kind="path", source_path="/work/c"),
        info(distribution="heinzel-d"),
    ]
    out = text(_render.render_list(plugins))
    assert "first-party" in out
    assert "layer acme" in out
    assert "theme acme.dark" in out
    assert "disabled" in out
    assert "git: https://github.com/x/b" in out
    assert "fails to load" in out
    assert "path: /work/c" in out
    assert "unknown" in out


def test_preview_already_shown_says_not_installed_and_how_to_install_later():
    out = text(_render.render_change(change(previewed=True)))
    assert "Not installed." in out
    assert "To install later: hz system plugin add crm --confirm" in out


def test_preview_lists_provenance_warnings_and_the_confirmation():
    out = text(
        _render.render_change(
            change(
                candidate=candidate(private=True, installed_version="1.0.0", warnings=["host not checked"]),
            )
        )
    )
    assert "private repository" in out
    assert "Replaces" in out
    assert "Warning: host not checked" in out
    assert "run with your rights" in out
    assert "Not installed yet: this was a preview." in out
    assert "To install: hz system plugin add crm --confirm" in out


def test_preview_of_a_first_party_plugin_omits_the_rights_warning():
    out = text(_render.render_change(change(candidate=candidate(first_party=True, distribution=None, version=None))))
    assert "first-party, git" in out
    assert "unknown until installed" in out
    assert "run with your rights" not in out


def test_confirmed_add_reports_needs_bindings_and_what_the_plugin_brings():
    installed = info(themes=["crm.dark"], kinds=["capability", "theme"], capabilities=["crm:tracker"])
    out = text(
        _render.render_change(
            change(
                done=True,
                plugins=[installed],
                needs=[need("installed")],
                bindings=[binding("written")],
                next=["hz system plugin remove heinzel-crm"],
            )
        )
    )
    assert "Installed heinzel-crm 1.2.0." in out
    assert "which heinzel-crm needs" in out
    assert "Adds the theme crm.dark" in out
    assert "Adds commands: hz crm --help" in out
    assert "Adds the integration crm:tracker." in out
    assert "tracker → github, as acme recommends." in out
    assert "Undo with: hz system plugin remove heinzel-crm" in out


def test_confirmed_add_without_a_listed_plugin_or_version_names_the_plugin_only():
    out = text(_render.render_change(change(done=True, distribution=None, candidate=candidate(version=None))))
    assert "Installed the plugin." in out
    assert "Adds the" not in out


def test_confirmed_add_of_a_distribution_not_in_the_listing_brings_nothing():
    out = text(_render.render_change(change(done=True, plugins=[info(distribution="heinzel-other")])))
    assert "Adds the" not in out
    assert "Adds commands" not in out


@pytest.mark.parametrize(
    ("action", "word"), [("remove", "Removed"), ("enable", "Enabled"), ("disable", "Disabled"), ("develop", "Added")]
)
def test_other_changes_say_what_was_done_and_when_to_restart(action, word):
    out = text(_render.render_change(change(action=action, done=True, candidate=None, next=[])))
    assert f"{word} heinzel-crm." in out
    assert "restart a running `hz ui serve`" in out


def test_other_change_without_restart_does_not_mention_it():
    out = text(_render.render_change(change(action="remove", done=True, candidate=None, restart_required=False)))
    assert "Removed heinzel-crm." in out
    assert "restart" not in out


def test_repair_lists_every_need_state_binding_and_the_message():
    needs = [
        need("updated", required=False),
        need("present"),
        need("offered", detail="add it with `hz system plugin add github`."),
        need("failed", detail="no network."),
        need("not-reached"),
    ]
    out = text(
        _render.render_repair(
            PluginRepair(
                needs=needs,
                restart_required=True,
                bindings=[binding("kept"), binding("conflict", adapter=None), binding("skipped")],
                message="Repaired one.",
            )
        )
    )
    assert "Updated heinzel-github 2.0.0, which heinzel-crm recommends." in out
    assert "was already installed" in out
    assert "heinzel-crm recommends heinzel-github; add it" in out
    assert "✗ heinzel-github, which heinzel-crm needs: no network." in out
    assert "needs: not-reached" in out
    assert "Kept tracker (yours: jira); acme recommends github." in out
    assert "Choose who serves tracker: yours: jira." in out
    assert "not set (yours: jira)" in out
    assert "Repaired one." in out


def test_written_bindings_come_with_one_undo_and_checklist_line_per_module():
    lines = _render._binding_lines([binding("written"), binding("written", capability="mail")])
    texts = [line.plain for line in lines]
    assert texts.count("Undo with: hz system plugin unbind acme") == 1
    assert texts.count("What is still yours to do: hz system plugin checklist --module acme") == 1


def test_bind_table_shows_each_binding_and_the_message():
    from heinzel.modules.system.plugin._setup_models import BindingResult

    out = text(
        _render.render_bind(
            SetupBind(
                bindings=[
                    BindingResult(capability="tracker", adapter="github", state="written", by="acme", reason="ok"),
                    BindingResult(capability="mail", adapter=None, state="conflict", by="acme", reason="layers"),
                ],
                message="One set.",
            )
        )
    )
    assert "github" in out
    assert "conflict" in out
    assert "One set." in out


def row(state, connector="github", **over):
    base = {
        "id": f"acme:{connector}",
        "connector": connector,
        "capabilities": ["tracker"],
        "state": state,
        "line": f"{connector} {state}",
        "action": RowAction(kind="sign-in", connector=connector, command=f"hz {connector} login"),
        "owner_only": state == "needs-attention",
    }
    return ChecklistRow(**(base | over))


def test_checklist_marks_each_state_and_gives_the_next_command_unless_connected():
    out = text(
        _render.render_checklist(
            Checklist(
                module="acme",
                rows=[row("connected"), row("not-connected", "jira"), row("needs-attention", "drive")],
                done=False,
            )
        )
    )
    assert "Finish setting up acme" in out
    assert "hz jira login" in out
    assert "hz drive login" in out
    assert "hz github login" not in out
    assert "yes" in out


def test_checklist_without_module_has_a_plain_title_and_done_says_so():
    undone = Checklist(module=None, rows=[row("not-installed")], done=False)
    assert "Finish setting up" in text(_render.render_checklist(undone))
    assert "Everything is set up." in text(_render.render_checklist(Checklist(module=None, rows=[], done=True)))
    assert "acme is set up." in text(_render.render_checklist(Checklist(module="acme", rows=[], done=True)))


def test_setup_result_shows_steps_and_the_checklist():
    out = text(
        _render.render_result(
            SetupResult(
                module="acme",
                steps=[SetupStep(name="bind", state="done", detail="two set")],
                bindings=[],
                checklist=Checklist(module="acme", rows=[row("not-connected")], done=False),
            )
        )
    )
    assert "Setting up acme" in out
    assert "two set" in out
    assert "hz github login" in out


# The module's public render functions load the presentation on demand and give the same result.


def test_module_renderers_delegate_to_the_presentation():
    unpin = PluginUnpin(distribution="d", version="1", done=False, confirmation=None, message="m", next=[])
    repair = PluginRepair(needs=[], restart_required=False, bindings=[], message="fine")
    checklist = Checklist(module=None, rows=[], done=True)
    assert text(plugin_module.render_unpin(unpin)) == text(_render.render_unpin(unpin))
    assert text(plugin_module.render_repair(repair)) == text(_render.render_repair(repair))
    assert text(plugin_module.render_change(change(previewed=True))) == text(
        _render.render_change(change(previewed=True))
    )
    assert text(plugin_module.render_list([])) == text(_render.render_list([]))
    assert text(plugin_module.render_search(PluginSearch(plugins=[], warnings=[]))) == text(
        _render.render_search(PluginSearch(plugins=[], warnings=[]))
    )
    assert text(plugin_module.render_checklist(checklist)) == text(_render.render_checklist(checklist))
    bind = SetupBind(bindings=[], message="none")
    assert text(plugin_module.render_bind(bind)) == text(_render.render_bind(bind))
    result = SetupResult(module="acme", steps=[], bindings=[], checklist=checklist)
    assert text(plugin_module.render_result(result)) == text(_render.render_result(result))


# The operations only pass their arguments on; a recording service shows exactly what each one asks for.


class Recorder:
    """Stands in for a service the operation delegates to: records the call and answers what it was given."""

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.answer(name, args, kwargs) if callable(self.answer) else self.answer

        return call


def test_list_reads_the_loaded_inventory_without_identity_and_fresh_needs_an_authenticated_head(tmp_path):
    service = Recorder([])
    user = CurrentUser(tmp_path)
    assert plugin_module.list_(service, user) == []
    with pytest.raises(HeinzelError, match="requires an authenticated head"):
        plugin_module.list_(service, user, fresh=True)
    with user.local("cli"):
        assert plugin_module.list_(service, user, fresh=True) == []
    assert service.calls == [("installed", (), {"fresh": False}), ("installed", (), {"fresh": True})]


def test_plain_operations_pass_arguments_to_the_plugin_service(tmp_path):
    service = Recorder("answer")
    assert plugin_module.showcase(service, "crm") == "answer"
    assert plugin_module.add_extra(service, "openai") == "answer"
    assert plugin_module.repair(service, automatic=True) == "answer"
    assert plugin_module.unpin(service, "heinzel-crm", confirm="abc") == "answer"
    assert plugin_module.pending(service) == "answer"
    assert plugin_module.operation(service, "op1") == "answer"
    assert plugin_module.develop(service, Path("/x")) == "answer"
    assert plugin_module.remove(service, "heinzel-crm") == "answer"
    assert plugin_module.disable(service, "heinzel-crm") == "answer"
    assert plugin_module.enable(service, "heinzel-crm") == "answer"
    assert plugin_module.history(service) == "answer"
    assert plugin_module.sdk(service, tmp_path) == "answer"
    assert plugin_module.search(service, "cal", refresh=True) == "answer"
    assert plugin_module.new(service, tmp_path, data=["a=b"], defaults=True, preset="contributions") == "answer"
    assert plugin_module.new(service, tmp_path) == "answer"
    names = [name for name, _, _ in service.calls]
    assert names == [
        "showcase",
        "add_extra",
        "repair",
        "unpin",
        "pending",
        "operation",
        "develop",
        "remove",
        "disable",
        "enable",
        "history",
        "sdk",
        "search",
        "new",
        "new",
    ]
    assert service.calls[3] == ("unpin", ("heinzel-crm",), {"confirm": "abc"})
    assert service.calls[12] == ("search", ("cal",), {"refresh": True})
    assert service.calls[13][2] == {"defaults": True, "layout": "standalone", "preset": "contributions"}
    assert service.calls[13][1][3] == ["a=b"]
    assert service.calls[14][1][3] == ()


def test_add_confirmed_or_without_a_person_returns_the_service_answer_as_is():
    preview = change()
    service = Recorder(preview)
    quiet = ScriptedPrompter()
    quiet.interactive = False
    assert plugin_module.add(service, quiet, "crm") is preview
    assert plugin_module.add(service, ScriptedPrompter(), "crm", confirm=True) is preview
    assert len(service.calls) == 2
    without_candidate = Recorder(change(candidate=None))
    assert plugin_module.add(without_candidate, ScriptedPrompter(), "crm").candidate is None


def test_add_at_a_terminal_asks_and_installs_on_yes():
    preview = change(operation_id="op1")
    done = change(done=True)
    service = Recorder(lambda _n, _a, kwargs: done if kwargs["confirm"] else preview)
    prompter = ScriptedPrompter(["y"])
    assert plugin_module.add(service, prompter, "crm", bind=False) is done
    assert any("Install heinzel-crm 1.2.0 now?" in line for line in prompter.transcript)
    assert service.calls[1][2]["operation_id"] == "op1"
    assert service.calls[1][2]["confirm"] is True
    assert service.calls[1][2]["bind"] is False


def test_add_at_a_terminal_answered_no_marks_the_preview_as_shown():
    service = Recorder(change())
    result = plugin_module.add(service, ScriptedPrompter(["n"]), "crm")
    assert result.previewed is True
    assert result.done is False
    assert len(service.calls) == 1


def test_setup_operations_pass_arguments_to_the_setup_service():
    service = Recorder("answer")
    assert plugin_module.setup(service, "acme", ["acme.region=lisbon"]) == "answer"
    assert plugin_module.setup(service, "acme") == "answer"
    assert plugin_module.checklist(service, "acme") == "answer"
    assert plugin_module.checklist(service) == "answer"
    assert plugin_module.unbind(service, "acme") == "answer"
    assert plugin_module.bind(service) == "answer"
    assert plugin_module.bind(
        service, ["heinzel-crm"], "crm", "op1", once=True, decline=True, dry_run=True, automatic=True
    )
    assert service.calls[0] == ("run", ("acme", {"acme.region": "lisbon"}), {})
    assert service.calls[1] == ("run", ("acme", {}), {})
    assert service.calls[4] == ("unbind", ("acme",), {})
    assert service.calls[5][2]["plugins"] == ()
    assert service.calls[6][2] == {
        "module": "crm",
        "plugins": ["heinzel-crm"],
        "operation_id": "op1",
        "once": True,
        "decline": True,
        "dry_run": True,
        "automatic": True,
    }


def test_parse_answers_reads_json_else_text_and_refuses_a_pair_without_equals():
    assert plugin_module.parse_answers(["a=1", "b=[1, 2]", "c=Lisbon", " d = true"]) == {
        "a": 1,
        "b": [1, 2],
        "c": "Lisbon",
        "d": True,
    }
    with pytest.raises(HeinzelError, match="key=value"):
        plugin_module.parse_answers(["nothing"])
    with pytest.raises(HeinzelError, match="key=value"):
        plugin_module.parse_answers(["=1"])
