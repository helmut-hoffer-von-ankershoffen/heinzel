"""The skills in effect along the layer stack, the allowlists, and the Claude Code export (ADR 0060)."""

import json
import stat
from pathlib import Path

import pytest
import yaml

from heinzel import HeinzelError, Module
from heinzel.kernel import Layer, LayerStack
from heinzel.modules.system.skill import SkillService, SkillSettings
from heinzel.modules.system.skill._export import MANIFEST, MARKER

pytestmark = pytest.mark.unit


def write_skill(root: Path, name: str, description: str = "Use it.", hints: str = "", **files: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{hints}---\n\n# {name}\n", encoding="utf-8"
    )
    for path, text in files.items():
        target = directory.joinpath(*path.split("__"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return directory


def plugin(tmp_path: Path, module_name: str, *skills: str, description: str = "Use it.", **files: str) -> Module:
    root = tmp_path / module_name
    for name in skills:
        write_skill(root, name, description, **files)
    module = Module(module_name, f"The {module_name} plugin.")
    module.skill(root)
    return module


def layer(name: str, parent: str | None = None) -> Layer:
    return Layer(name=name, module=name, distribution=f"heinzel-{name}", parent=parent, site=None, label="team")


@pytest.fixture
def service(make_kernel, tmp_path):
    def make(*modules: Module, stack: tuple[str, ...] = (), **settings: object) -> SkillService:
        kernel = make_kernel(builtins=True, **{m.name: m for m in modules})
        if stack:
            kernel.layer_stack = lambda *a, **k: LayerStack(  # type: ignore[method-assign]
                layer=stack[-1], layers=tuple(layer(n) for n in stack)
            )
        values = {"personal_dir": tmp_path / "mine", "export_dirs": [tmp_path / "claude"]} | settings
        return SkillService(kernel, SkillSettings(**values))

    return make


def test_the_core_ships_explain_heinzel_and_the_chat_gets_the_core_skills(service):
    svc = service()
    listed = svc.listing()
    core = next(view for view in listed.skills if view.name == "explain-heinzel")
    assert (core.source, core.module, core.plugin, core.chat) == ("core", "system.skill", None, True)
    assert svc.chat_allowed() == ["explain-heinzel"]
    assert service(chat=["other"]).chat_allowed() == ["other"]
    assert listed.next == ["hz system skill show explain-heinzel", "hz system skill export --name explain-heinzel"]


def test_a_plugin_outside_the_stack_never_hides_a_core_skill_but_a_layer_does(service, tmp_path):
    lookalike = plugin(tmp_path, "lookalike", "explain-heinzel", description="Forged.")
    offered = service(lookalike).catalog().skills["explain-heinzel"]
    assert (offered.source, offered.module, offered.shadows) == ("core", "system.skill", ["lookalike"])
    team = plugin(tmp_path, "acme-team", "explain-heinzel", description="The team's version.")
    offered = service(team, stack=("acme-team",)).catalog().skills["explain-heinzel"]
    assert (offered.source, offered.skill.description, offered.shadows) == (
        "layer",
        "The team's version.",
        ["system.skill"],
    )


def test_the_more_specific_layer_wins_and_personal_skills_win_over_all(service, tmp_path):
    company = plugin(tmp_path, "acme", "notes", description="Company.")
    team = plugin(tmp_path, "acme-team", "notes", description="Team.")
    svc = service(company, team, stack=("acme", "acme-team"))
    offered = svc.catalog().skills["notes"]
    assert (offered.skill.description, offered.shadows) == ("Team.", ["acme"])
    write_skill(tmp_path / "mine", "notes", "Mine.")
    offered = svc.catalog().skills["notes"]
    assert (offered.source, offered.skill.description, offered.shadows) == ("personal", "Mine.", ["acme-team", "acme"])


def test_two_plugins_outside_the_stack_tie_by_module_name(service, tmp_path):
    first, second = plugin(tmp_path, "alpha", "notes", description="A."), plugin(tmp_path, "beta", "notes")
    assert service(second, first).catalog().skills["notes"].module == "alpha"


def test_an_org_and_the_site_offering_one_name_is_a_conflict(service, tmp_path, make_kernel):
    org, site = plugin(tmp_path, "acme-pay", "notes"), plugin(tmp_path, "acme-lisbon", "notes")
    kernel = make_kernel(builtins=True, **{"acme-pay": org, "acme-lisbon": site})
    kernel.layer_stack = lambda *a, **k: LayerStack(  # type: ignore[method-assign]
        layer="acme-pay-team",
        layers=(layer("acme"), layer("acme-pay", "acme"), layer("acme-lisbon", "acme"), layer("acme-pay-team")),
        site="acme-lisbon",
        site_ancestors=frozenset({"acme"}),
    )
    catalog = SkillService(kernel, SkillSettings(personal_dir=tmp_path / "none")).catalog()
    assert "notes" not in catalog.skills
    assert catalog.conflicts[0].startswith("notes: offered by acme-lisbon and acme-pay")


def test_a_personal_skill_that_fails_its_checks_is_a_problem_not_an_error(service, tmp_path):
    write_skill(tmp_path / "mine", "hz-mine")
    (tmp_path / "mine" / ".hidden").mkdir()
    svc = service()
    assert svc.listing().problems == [
        "Skill 'hz-mine': skill name 'hz-mine' can't start with hz or heinzel: those are hz's own"
    ]
    assert "hz-mine" not in svc.catalog().skills


def test_a_personal_dir_inside_an_export_dir_is_refused(service, tmp_path):
    write_skill(tmp_path / "claude" / "mine", "notes")
    svc = service(personal_dir=tmp_path / "claude" / "mine")
    assert svc.catalog().problems[0].endswith("where exports go; keep your own skills elsewhere")


def test_index_load_and_read_enforce_the_allowlist_in_code(service, tmp_path):
    svc = service(plugin(tmp_path, "notes", "review-notes", references__list="- a@example.com\n"))
    assert [e.name for e in svc.index(["review-notes"])] == ["review-notes"]
    assert {e.name for e in svc.index()} >= {"review-notes", "explain-heinzel"}
    loaded = svc.load("review-notes", ["review-notes"])
    assert (loaded.source, loaded.module, loaded.files) == ("plugin", "notes", ["references/list"])
    assert svc.read_file("review-notes", "references/list", ["review-notes"]) == "- [email]\n"
    with pytest.raises(HeinzelError, match="isn't one this may use; it may use explain-heinzel"):
        svc.load("review-notes", ["explain-heinzel"])
    with pytest.raises(HeinzelError, match="isn't one this may use"):
        svc.read_file("review-notes", "references/list", [])
    with pytest.raises(HeinzelError, match="No skill is called 'gone'"):
        svc.load("gone")
    with pytest.raises(HeinzelError, match="ships no file"):
        svc.read_file("review-notes", "SECRET.md")


def test_show_returns_the_body_cut_and_clean(service, tmp_path, monkeypatch):
    svc = service(plugin(tmp_path, "notes", "review-notes", scripts__run="echo\n"))
    detail = svc.show("review-notes")
    assert (detail.body, detail.skill.scripts) == ("# review-notes\n", 1)
    monkeypatch.setattr("heinzel.modules.system.skill._service.MAX_SKILL_LOAD", 5)
    assert svc.load("review-notes").body == "# rev\n…(cut)"


def test_export_writes_a_marked_private_copy_without_hints(service, tmp_path):
    hints = "allowed-tools: Bash\nmodel: opus\n"
    root = tmp_path / "notes"
    write_skill(root, "review-notes", hints=hints, references__list="- a\n")
    module = Module("notes", "Notes.")
    module.skill(root)
    svc = service(module)
    result = svc.export(["review-notes"])
    target = tmp_path / "claude" / "heinzel-review-notes"
    assert [(i.name, i.action, i.target) for i in result.items] == [("review-notes", "exported", target)]
    text = (target / "SKILL.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---")[1])
    assert meta == {"name": "heinzel-review-notes", "description": "Use it."}
    assert (target / "references" / "list").read_text(encoding="utf-8") == "- a\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert stat.S_IMODE((target / "SKILL.md").stat().st_mode) == 0o600
    assert json.loads((target / MARKER).read_text(encoding="utf-8"))["name"] == "review-notes"
    manifest = json.loads((svc._kernel.settings.state_dir / MANIFEST).read_text(encoding="utf-8"))
    assert manifest == {
        str(tmp_path / "claude"): {"review-notes": svc.catalog().skills["review-notes"].skill.fingerprint}
    }
    assert svc.listing().skills[-1].exported == [str(target)]
    again = svc.export(["review-notes"])
    assert again.items[0].action == "exported"
    assert sorted(p.name for p in (tmp_path / "claude").iterdir()) == ["heinzel-review-notes"]


def test_export_leaves_alone_what_hz_did_not_write(service, tmp_path):
    svc = service(plugin(tmp_path, "notes", "review-notes"))
    theirs = tmp_path / "claude" / "heinzel-review-notes"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("mine", encoding="utf-8")
    item = svc.export(["review-notes"]).items[0]
    assert (item.action, (theirs / "SKILL.md").read_text(encoding="utf-8")) == ("conflict", "mine")
    assert "isn't an export of hz's" in item.detail
    (theirs / MARKER).write_text("{}", encoding="utf-8")  # a marker hz's manifest doesn't list: still not ours
    assert "isn't in hz's list" in svc.export(["review-notes"]).items[0].detail


def test_export_refuses_linked_targets_and_directories(service, tmp_path):
    svc = service(plugin(tmp_path, "notes", "review-notes"))
    (tmp_path / "claude").mkdir()
    (tmp_path / "claude" / "heinzel-review-notes").symlink_to(tmp_path)
    assert "symbolic link" in svc.export(["review-notes"]).items[0].detail
    (tmp_path / "real").mkdir()
    linked = service(plugin(tmp_path / "p2", "notes", "review-notes"), export_dirs=[tmp_path / "link"])
    (tmp_path / "link").symlink_to(tmp_path / "real")
    item = linked.export(["review-notes"]).items[0]
    assert (item.action, list((tmp_path / "real").iterdir())) == ("conflict", [])


def test_export_needs_names_or_all_and_all_leaves_personal_skills_out(service, tmp_path):
    write_skill(tmp_path / "mine", "my-notes")
    svc = service(plugin(tmp_path, "notes", "review-notes"))
    with pytest.raises(HeinzelError, match="Name the skills to export"):
        svc.export([])
    with pytest.raises(HeinzelError, match="No skill is called gone"):
        svc.export(["gone"])
    with pytest.raises(HeinzelError, match="only removes stale exports"):
        svc.export(["review-notes"], sync=True)
    exported = {item.name for item in svc.export([], everything=True).items}
    assert exported == {"explain-heinzel", "review-notes"}
    assert svc.export(["my-notes"]).items[0].action == "exported"


def test_sync_removes_gone_and_changed_exports_and_never_adds(service, tmp_path):
    svc = service(plugin(tmp_path, "notes", "review-notes"))
    svc.export(["review-notes", "explain-heinzel"])
    claude = tmp_path / "claude"
    changed = service(plugin(tmp_path / "v2", "notes", "review-notes", description="Changed."))
    result = changed.export([], sync=True)
    assert [(i.name, i.action) for i in result.items] == [("review-notes", "changed")]
    assert "export --name review-notes" in result.items[0].detail
    assert sorted(p.name for p in claude.iterdir()) == ["heinzel-explain-heinzel"]
    assert service().export([], sync=True).items == []  # the core skill's export is unchanged: kept
    (tmp_path / "x").mkdir()
    lost = service(plugin(tmp_path / "x", "other", "other-notes"))
    lost.export(["other-notes"])
    assert [(i.name, i.action) for i in service().export([], sync=True).items] == [("other-notes", "removed")]


def test_unexport_removes_only_hz_exports(service, tmp_path):
    svc = service(plugin(tmp_path, "notes", "review-notes"))
    with pytest.raises(HeinzelError, match="Name the skills to remove"):
        svc.unexport([])
    svc.export(["review-notes", "explain-heinzel"])
    marker = tmp_path / "claude" / "heinzel-explain-heinzel" / MARKER
    marker.unlink()
    result = svc.unexport([], everything=True)
    assert sorted((i.name, i.action) for i in result.items) == [
        ("explain-heinzel", "conflict"),
        ("review-notes", "removed"),
    ]
    assert sorted(p.name for p in (tmp_path / "claude").iterdir()) == ["heinzel-explain-heinzel"]
    assert svc.unexport(["review-notes"]).items == []


def test_a_broken_manifest_is_an_empty_one(service, tmp_path):
    svc = service()
    path = svc._kernel.settings.state_dir / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    for content in ("not json", "[1]", '{"a": 1}'):
        path.write_text(content, encoding="utf-8")
        assert svc.unexport([], everything=True).items == []


def test_an_export_directory_that_cannot_be_created_is_a_conflict(service, tmp_path):
    (tmp_path / "afile").write_text("x", encoding="utf-8")
    svc = service(plugin(tmp_path, "notes", "review-notes"), export_dirs=[tmp_path / "afile"])
    item = svc.export(["review-notes"]).items[0]
    assert (item.action, "can't be created" in item.detail) == ("conflict", True)


def test_a_manifest_entry_outside_the_export_dirs_or_badly_named_is_never_acted_on(service, tmp_path):
    svc = service()
    elsewhere = tmp_path / "elsewhere" / "heinzel-x"
    elsewhere.mkdir(parents=True)
    (elsewhere / MARKER).write_text("{}", encoding="utf-8")
    path = svc._kernel.settings.state_dir / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {str(tmp_path / "elsewhere"): {"x": "f"}, str(tmp_path / "claude"): {"../../etc": "f"}}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert svc.unexport([], everything=True).items == []
    assert elsewhere.is_dir()
    assert str(tmp_path / "elsewhere") in json.loads(path.read_text(encoding="utf-8"))


def test_health_names_problems_without_skill_text(service, tmp_path):
    from heinzel import Status
    from heinzel.modules.system.skill import health

    assert health(service())[0].status == Status.UP
    write_skill(tmp_path / "mine", "notes", hints="model: [a, {b: c}]\n")
    check = health(service())[0]
    assert (check.status, check.fix) == (Status.DEGRADED, "hz system skill list")
    assert "notes" in check.detail
    assert "# notes" not in check.detail
