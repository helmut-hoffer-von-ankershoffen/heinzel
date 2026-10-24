"""The workflow module's side of playbooks: discovery, template lookup and the setup walk (ADR 0037)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import pytest

from heinzel import (
    AssetType,
    Config,
    ContractTemplate,
    DiscoveryHint,
    Document,
    HeinzelError,
    Issue,
    Module,
    PlaybookLet,
    Settings,
    Window,
)
from heinzel.kernel import ScriptedPrompter
from heinzel.modules.workflow import WorkflowService, WorkflowSettings
from heinzel.modules.workflow import _playbooks as playbooks

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 30, 17, 0, tzinfo=UTC)
TEMPLATE = Path(__file__).parents[2] / "fixtures" / "playbook" / "templates" / "weekly-report.prose.md"


class Docs:
    def read(self, ref: str) -> Document:
        if ref == "boom":
            msg = "the vendor is down"
            raise RuntimeError(msg)
        return Document(ref=ref, title=f"Team update {ref}", text="x")


class Tracker:
    windows: ClassVar[list[Window]] = []

    def issues_updated(self, project: str, window: Window) -> list[object]:
        Tracker.windows.append(window)
        many = [
            Issue(key=f"{project}-{i}", title=f"Weekly report {i}", url="u", state="open", updated_at=window.until)
            for i in range(30)
        ]
        return ["not an item", {"title": "Weekly report without a reference"}, *many]


class Editor:
    def __init__(self, refuse: str | None = None) -> None:
        self.written: dict[str, object] = {}
        self.refuse = refuse

    def write(self, key: str, value: object, scope: str = "user") -> Path:
        if key == self.refuse:
            msg = f"{key}: no such adapter"
            raise HeinzelError(msg)
        self.written[key] = value
        return Path("/config.toml")


def plugin(name: str = "teamwork", *, template: bool = True, defaults: bool = True, adapters: bool = True) -> Module:
    module = Module(name, "Test plugin.")
    if adapters:  # one plugin brings them; two plugins bringing the same adapter names would be ambiguous
        module.provides("documents", modes={"read"}, name="docs")(Docs)
        module.provides("tracker", modes={"read"}, name="tracker")(Tracker)
    module.playbook(
        "For tests.",
        assets=[
            AssetType(
                name="weekly-report",
                description="The report document.",
                hints=[
                    DiscoveryHint(read="documents.read", args={"ref": "doc-1"}, match="(?i)team update"),
                    DiscoveryHint(read="documents.read", args={"ref": "boom"}),
                    DiscoveryHint(read="documents.read", args={"nope": "x"}),
                ],
            ),
            AssetType(
                name="team-tracker",
                description="Issues about the report.",
                hints=[DiscoveryHint(read="tracker.issues_updated", args={"project": "APP"}, window="7d")],
            ),
            AssetType(name="team-repository", description="Named by hand."),
            AssetType(name="team-project", description="Named by hand."),
        ],
        templates=[TEMPLATE] if template else [],
        defaults={"capabilities.documents": "docs", f"{name}.region": "eu"} if defaults else {},
    )
    return module


@pytest.fixture
def make(make_kernel, tmp_path):
    Tracker.windows.clear()

    def build(*modules: Module, bindings: dict[str, str] | None = None) -> WorkflowService:
        capabilities = {"agent_runner": "claude-code", "documents": "docs", "tracker": "tracker"}
        data = {"capabilities": capabilities if bindings is None else bindings, "workflow": {"user_dir": str(tmp_path)}}
        named = {m.name: m for m in modules or (plugin(),)}
        kernel = make_kernel(builtins=True, config=Config(files=(), data=data), **named)
        settings = Config(files=(), data={}).settings(Settings, "hz")
        return WorkflowService(kernel, kernel.resolve(WorkflowSettings), settings, clock=lambda: NOW)

    return build


def the_playbook(service: WorkflowService, name: str = "teamwork") -> PlaybookLet:
    return next(p for q, p in playbooks.playbooks(service._kernel) if q == name)


def test_discovery_keeps_matching_items_with_a_reference_up_to_the_cap(make):
    found = make().discover()
    refs = [(c.playbook, c.asset, c.ref) for c in found.candidates]
    assert refs[0] == ("teamwork", "weekly-report", "doc-1")
    tracked = [r for r in refs if r[1] == "team-tracker"]
    assert len(tracked) == playbooks.MAX_CANDIDATES  # the non-item and the item without a reference are skipped
    assert tracked[0][2] == "APP-0"
    assert found.playbooks == ["teamwork"]


def test_a_hint_reads_its_window_ending_now_by_the_services_clock(make):
    make().discover()
    (window,) = Tracker.windows
    assert (window.until, window.until - window.since) == (NOW, timedelta(days=7))


def test_each_failing_hint_is_its_own_problem(make):
    problems = {p.problem.split(":")[0]: p for p in make().discover().problems}
    assert set(problems) == {"RuntimeError", "The hint doesn't fit documents.read"}
    assert "report it to the authors of teamwork" in problems["The hint doesn't fit documents.read"].problem


def test_an_unbound_capability_is_one_problem_per_playbook_with_its_fix(make):
    found = make(bindings={"agent_runner": "claude-code"}).discover("teamwork")
    assert found.candidates == []
    unbound = [p for p in found.problems if "fit" not in p.problem]
    assert len(unbound) == 2  # documents and tracker, once each, though documents has three hints
    assert all("hz system config set capabilities." in p.problem for p in unbound)


def test_discovery_of_one_playbook_leaves_the_others_alone(make):
    found = make(plugin("teamwork"), plugin("other", adapters=False)).discover("other")
    assert {c.playbook for c in found.candidates} == {"other"}
    assert [s.command for s in found.next] == ["hz workflow new weekly-report --template other:weekly-report"]


def test_discovery_without_playbooks_points_at_plugins(make):
    found = make(Module("plain", "No playbook.")).discover()
    assert (found.playbooks, found.candidates) == ([], [])
    assert found.next[0].command == "hz system plugin search --text playbook"
    with pytest.raises(HeinzelError, match="No playbook 'nope'"):
        make().discover("nope")


def test_templates_are_found_by_qualified_or_unique_bare_name(make):
    service = make(plugin("teamwork"), plugin("other", adapters=False))
    with pytest.raises(HeinzelError, match="say other:weekly-report or teamwork:weekly-report"):
        service.new("x", template="weekly-report")
    written = service.new("x", template="other:weekly-report")
    assert (written.template, written.unfilled[0]) == ("other:weekly-report", "team-repository")
    with pytest.raises(HeinzelError, match="none: no installed plugin contributes a playbook"):
        make(plugin(template=False)).new("y", template="weekly-report")


def test_assets_need_a_template_and_must_be_its_placeholders(make):
    with pytest.raises(HeinzelError, match="--asset fills a template's placeholders"):
        make().new("x", assets={"weekly-report": "doc-1"})
    with pytest.raises(HeinzelError, match="has no placeholder for the asset 'y'"):
        make().new("x", template="weekly-report", assets={"y": "z"})


def test_placeholders_left_are_errors_until_filled(make, tmp_path):
    service = make()
    service.new("report", template="weekly-report", assets={"weekly-report": "doc-1"})
    codes = [(d.code, d.field) for d in service.find("report").diagnostics]
    assert ("HZC025", "hz.inputs.merged.args.repository") in codes
    assert "weekly-report" not in str(codes)
    assert not service.show("report").ready


def test_a_template_that_fails_its_checks_is_reported_to_its_authors(make, tmp_path):
    module = Module("broken", "Test plugin.")
    module.playbook("Broken.", templates=[ContractTemplate(name="bad", text="no frontmatter")])
    with pytest.raises(HeinzelError, match="The template broken:bad doesn't pass its own checks"):
        make(module).new("bad", template="broken:bad")
    assert not (tmp_path / "bad.prose.md").exists()


def test_a_template_with_only_warnings_is_written(make, tmp_path):
    text = TEMPLATE.read_text().replace("### Steps", "### Notes")  # an unknown section is a warning
    module = Module("warned", "Test plugin.")
    module.playbook(
        "Warned.",
        assets=[AssetType(name=n, description="d") for n in ("team-repository", "team-project", "weekly-report")]
        + [AssetType(name="team-tracker", description="d")],
        templates=[ContractTemplate(name="report", text=text)],
    )
    assert make(module).new("report", template="warned:report").file.is_file()


def test_setup_asks_for_each_setting_and_writes_only_what_is_accepted(make, tmp_path):
    service = make()
    editor = Editor()
    prompter = ScriptedPrompter(["y", "n", "1", "21", "", "Bad Name", "team-report"])
    service.setup(the_playbook(service), prompter, editor)
    assert editor.written == {"capabilities.documents": "docs"}
    text = (tmp_path / "team-report.prose.md").read_text()
    assert 'args: {ref: "doc-1"}' in text
    assert "{{asset:team-tracker}}" in text  # "fill it in later" was chosen
    said = "\n".join(prompter.transcript)
    assert "couldn't run" in said
    assert "count from the next command" in said
    assert "Wrote" in said


def test_setup_reports_a_refused_setting_and_goes_on(make, tmp_path):
    service = make()
    prompter = ScriptedPrompter(["y", "y", "2", "21", "n"])
    editor = Editor(refuse="capabilities.documents")
    service.setup(the_playbook(service), prompter, editor)
    assert editor.written == {"teamwork.region": "eu"}
    assert "Not saved: capabilities.documents: no such adapter" in "\n".join(prompter.transcript)
    assert list(tmp_path.glob("*.prose.md")) == []


def test_setup_leaves_an_existing_contract_alone(make, tmp_path):
    service = make()
    (tmp_path / "weekly-report.prose.md").write_text("mine")
    prompter = ScriptedPrompter(["n", "n", "2", "21", "", ""])
    service.setup(the_playbook(service), prompter, Editor())
    assert (tmp_path / "weekly-report.prose.md").read_text() == "mine"
    assert "weekly-report exists; left as it is." in "\n".join(prompter.transcript)


def test_setup_of_a_playbook_without_settings_or_templates_asks_nothing(make):
    service = make(plugin(template=False, defaults=False))
    prompter = ScriptedPrompter([])
    service.setup(the_playbook(service), prompter, Editor())
    assert not any("?" in line for line in prompter.transcript)


def test_setup_refuses_a_playbook_no_enabled_module_contributes(make):
    with pytest.raises(HeinzelError, match="isn't contributed by an enabled module"):
        make().setup(PlaybookLet(name="ghost", summary="s"), ScriptedPrompter([]), Editor())  # type: ignore[arg-type]


def test_the_listing_without_playbooks_points_at_plugins_and_writing_one(make):
    listed = make(Module("plain", "No playbook.")).playbooks()
    assert listed.playbooks == []
    assert [s.command for s in listed.next] == [
        "hz system plugin search --text playbook",
        "hz system plugin new ../my-playbook",
    ]


def test_recommended_values_are_quoted_for_the_shell_when_they_arent_plain_words(make):
    (playbook,) = make().playbooks().playbooks
    assert [d.command for d in playbook.defaults] == [
        "hz system config set capabilities.documents docs",
        "hz system config set teamwork.region eu",
    ]
    assert playbooks._shell_value(["a b"]) == "'[\"a b\"]'"
