"""Playbooks as data: asset types, discovery hints, template contracts and recommended settings (ADR 0037)."""

from pathlib import Path

import pytest

from heinzel import AssetType, ContractTemplate, DiscoveryHint, Module, PlaybookLet, SetupLet
from heinzel.kernel import PlaybookSetup
from heinzel.kernel._playbooks import templates_in

pytestmark = pytest.mark.unit

TEXT = '---\nname: report\nhz:\n  inputs:\n    a: {read: documents.read, args: {ref: "{{asset:doc}}"}}\n---\n'


def test_a_hint_must_be_a_capabilitys_read_method_with_a_valid_window_and_pattern():
    hint = DiscoveryHint(read="tracker.issues_updated", args={"project": "APP"}, window="30d", match="(?i)report")
    assert dict(hint.args) == {"project": "APP"}
    for bad in ("tracker", "tracker.nope", "documents.replace_section", "nope.read", "agent-runner.run"):
        with pytest.raises(ValueError, match=r"read method|agent-runner"):
            DiscoveryHint(read=bad)
    with pytest.raises(ValueError, match=r"(?i)duration"):
        DiscoveryHint(read="tracker.issues_updated", window="soon")
    with pytest.raises(ValueError, match="isn't a regular expression"):
        DiscoveryHint(read="documents.read", match="(")


def test_asset_types_and_templates_have_slugs_and_content():
    with pytest.raises(ValueError, match="lowercase words"):
        AssetType(name="Weekly Report", description="x")
    with pytest.raises(ValueError, match="needs a description"):
        AssetType(name="weekly-report", description=" ")
    with pytest.raises(ValueError, match="lowercase words"):
        ContractTemplate(name="x_y", text="t")
    with pytest.raises(ValueError, match="is empty"):
        ContractTemplate(name="x", text="\n")


def test_a_template_fills_its_placeholders_json_escaped():
    template = ContractTemplate(name="report", text=TEXT)
    assert template.placeholders() == ["doc"]
    assert 'ref: "a\\"b"' in template.fill({"doc": 'a"b'})
    assert template.fill({}) == TEXT


def test_templates_come_from_files_and_directories(tmp_path):
    (tmp_path / "one.prose.md").write_text(TEXT)
    (tmp_path / "notes.md").write_text("ignored")
    single = tmp_path / "single"
    single.mkdir()
    (single / "two.prose.md").write_text(TEXT)
    found = templates_in([tmp_path, single / "two.prose.md", ContractTemplate(name="three", text=TEXT)])
    assert [t.name for t in found] == ["one", "three", "two"]
    with pytest.raises(ValueError, match="Two templates are named 'one'"):
        templates_in([tmp_path, tmp_path / "one.prose.md"])
    with pytest.raises(ValueError, match=r"must be a \.prose\.md file"):
        ContractTemplate.from_file(tmp_path / "notes.md")


def test_a_playbook_refuses_what_doesnt_fit_together():
    doc = AssetType(name="doc", description="A document.")
    template = ContractTemplate(name="report", text=TEXT)
    with pytest.raises(ValueError, match="needs a summary"):
        PlaybookLet(name="p", summary="")
    with pytest.raises(ValueError, match="two asset types named 'doc'"):
        PlaybookLet(name="p", summary="s", assets=(doc, doc))
    with pytest.raises(ValueError, match="isn't a setting key"):
        PlaybookLet(name="p", summary="s", defaults={"tracker": "github"})
    with pytest.raises(ValueError, match="unknown asset type 'doc'"):
        PlaybookLet(name="p", summary="s", templates=(template,))
    with pytest.raises(ValueError, match="kept by an unknown template 'nope'"):
        PlaybookLet(name="p", summary="s", assets=(AssetType(name="doc", description="d", kept_by="nope"),))
    let = PlaybookLet(name="p", summary="s", assets=[doc], templates=[template], defaults={"capabilities.tracker": "x"})
    assert (let.assets, let.templates, dict(let.defaults)) == ((doc,), (template,), {"capabilities.tracker": "x"})


def test_module_playbook_declares_the_let_and_a_setup_step_that_asks_for_the_service(tmp_path):
    (tmp_path / "report.prose.md").write_text(TEXT)
    module = Module("teamwork", "Test.")
    let = module.playbook("For tests.", assets=[AssetType(name="doc", description="d")], templates=[tmp_path])
    assert [type(x) for x in module.lets] == [PlaybookLet, SetupLet]
    (step,) = [x for x in module.lets if isinstance(x, SetupLet)]
    assert step.summary == "Playbook: For tests."
    ran: list[object] = []

    class Recording(PlaybookSetup):
        def run(self, playbook, prompter, editor) -> None:
            ran.append(playbook)

    step.func(Recording(), None, None)
    assert ran == [let]
    with pytest.raises(NotImplementedError):
        PlaybookSetup().run(let, None, None)  # type: ignore[arg-type]
    quiet = Module("quiet", "Test.")
    quiet.playbook("No setup.", setup=False)
    assert [type(x) for x in quiet.lets] == [PlaybookLet]
    with pytest.raises(ValueError, match="already has a PlaybookLet"):
        quiet.playbook("Again.", setup=False)


def test_templates_are_read_at_import_from_the_plugins_directory():
    fixtures = Path(__file__).parents[2] / "fixtures" / "playbook" / "templates"
    (template,) = templates_in([fixtures])
    assert template.name == "weekly-report"
    assert template.placeholders() == ["team-repository", "team-project", "weekly-report", "team-tracker"]
