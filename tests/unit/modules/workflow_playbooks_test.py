"""The workflow module's side of playbooks: discovery, template lookup and the setup walk (ADR 0037)."""

from datetime import UTC, datetime
from pathlib import Path

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
    def issues_updated(self, project: str, window: Window) -> list[object]:
        many = [
            Issue(key=f"{project}-{i}", title=f"Weekly report {i}", url="u", state="open", updated_at=window.until)
            for i in range(30)
        ]
        return [*many, "not an item"]


class Editor:
    def __init__(self) -> None:
        self.written: dict[str, object] = {}

    def write(self, key: str, value: object, scope: str = "user") -> Path:
        self.written[key] = value
        return Path("/config.toml")


def plugin(name: str = "teamwork", *, template: bool = True) -> Module:
    module = Module(name, "Test plugin.")
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
        defaults={"capabilities.documents": "docs", "workflow.max_usd": 2.0},
    )
    return module


@pytest.fixture
def make(make_kernel, tmp_path):
    def build(*modules: Module, bindings: dict[str, str] | None = None) -> WorkflowService:
        capabilities = {"agent_runner": "claude-code", "documents": "docs", "tracker": "tracker"}
        data = {"capabilities": capabilities if bindings is None else bindings, "workflow": {"user_dir": str(tmp_path)}}
        named = {m.name: m for m in modules or (plugin(),)}
        kernel = make_kernel(builtins=True, config=Config(files=(), data=data), **named)
        settings = Config(files=(), data={}).settings(Settings, "hz")
        return WorkflowService(kernel, kernel.resolve(WorkflowSettings), settings, clock=lambda: NOW)

    return build


def test_discovery_keeps_matching_items_with_a_reference_and_reports_each_failing_hint(make):
    found = make().discover()
    refs = [(c.asset, c.ref) for c in found.candidates]
    assert refs[0] == ("teamwork:weekly-report", "doc-1")
    assert len([r for r in refs if r[0] == "teamwork:team-tracker"]) == playbooks.MAX_CANDIDATES
    problems = {p.problem.split(":")[0] for p in found.problems}
    assert problems == {"RuntimeError", "the hint doesn't fit documents.read"}
    assert [s.command for s in found.next] == ["hz workflow new weekly-report --template teamwork:weekly-report"]


def test_a_hint_whose_capability_isnt_served_says_how_to_serve_it(make):
    found = make(bindings={"agent_runner": "claude-code"}).discover("teamwork")
    assert found.candidates == []
    assert all("Fix: hz system config set capabilities." in p.problem for p in found.problems if "fit" not in p.problem)


def test_templates_are_found_by_qualified_or_unique_bare_name(make):
    service = make(plugin("teamwork"), plugin("other"))
    with pytest.raises(HeinzelError, match="say other:weekly-report or teamwork:weekly-report"):
        service.new("x", template="weekly-report")
    assert service.new("x", template="other:weekly-report").name == "x"
    with pytest.raises(HeinzelError, match="none: no installed plugin contributes a playbook"):
        make(plugin(template=False)).new("y", template="weekly-report")


def test_a_template_that_fails_its_checks_is_reported_to_its_authors(make, tmp_path):
    module = Module("broken", "Test plugin.")
    module.playbook("Broken.", templates=[ContractTemplate(name="bad", text="no frontmatter")])
    with pytest.raises(HeinzelError, match="The template broken:bad doesn't pass its own checks"):
        make(module).new("bad", template="broken:bad")
    assert not (tmp_path / "bad.prose.md").exists()


def test_setup_walks_settings_assets_and_templates_and_writes_only_what_is_accepted(make, tmp_path):
    service = make()
    let = next(p for _, p in playbooks.playbooks(service._kernel))
    editor = Editor()
    prompter = ScriptedPrompter(["y", "1", "21", "", "Bad Name", "team-report"])
    service.setup(let, prompter, editor)
    assert editor.written == {"capabilities.documents": "docs", "workflow.max_usd": 2.0}
    text = (tmp_path / "team-report.prose.md").read_text()
    assert 'args: {ref: "doc-1"}' in text
    assert "{{asset:team-tracker}}" in text  # "fill it in later" was chosen
    said = "\n".join(prompter.transcript)
    assert "couldn't run" in said
    assert "count from the next command" in said
    assert "Wrote" in said
    prompter = ScriptedPrompter(["n", "2", "21", "n"])
    service.setup(let, prompter, Editor())
    assert sorted(p.name for p in tmp_path.glob("*.prose.md")) == ["team-report.prose.md"]


def test_setup_refuses_a_playbook_no_enabled_module_contributes(make):
    with pytest.raises(HeinzelError, match="isn't contributed by an enabled module"):
        make().setup(PlaybookLet(name="ghost", summary="s"), ScriptedPrompter([]), Editor())  # type: ignore[arg-type]


def test_the_listing_without_playbooks_points_at_plugins(make):
    listed = make(Module("plain", "No playbook.")).playbooks()
    assert listed.playbooks == []
    assert listed.next[0].command == "hz system plugin search --text playbook"


def test_recommended_values_are_quoted_for_the_shell_when_they_arent_plain_words(make):
    (playbook,) = make().playbooks().playbooks
    assert [d.command for d in playbook.defaults] == [
        "hz system config set capabilities.documents docs",
        "hz system config set workflow.max_usd '2.0'",
    ]
