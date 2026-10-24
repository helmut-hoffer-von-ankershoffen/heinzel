import pytest

from heinzel import HeinzelError, Progress
from heinzel.modules.system.agent import AgentService, AgentSettings
from heinzel.modules.system.agent import _service as service_module
from heinzel.modules.system.agent._service import parse_frontmatter

pytestmark = pytest.mark.unit

SKILL = "---\nname: demo\ndescription: Do the demo thing. Use when asked.\n---\n\n# Demo\n"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (SKILL, {"name": "demo", "description": "Do the demo thing. Use when asked."}),
        ("no frontmatter", {}),
        ("---\nname: unterminated\n", {}),
        ("---\nname: x\n  nested: ignored\nurl: https://a:b\n---\n", {"name": "x", "url": "https://a:b"}),
    ],
)
def test_parse_frontmatter(text, expected):
    assert parse_frontmatter(text) == expected


@pytest.fixture
def skills_dir(tmp_path):
    root = tmp_path / "skills"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (root / "demo" / "references").mkdir()
    (root / "demo" / "references" / "notes.md").write_text("notes", encoding="utf-8")
    return root


def test_lists_skills(skills_dir):
    [skill] = AgentService(AgentSettings(), Progress(), skills_dir=skills_dir).skills()
    assert (skill.name, skill.path) == ("demo", skills_dir / "demo")


def test_install_copies_skills_into_every_directory_and_replaces_old_copies(skills_dir, tmp_path):
    targets = [tmp_path / "claude", tmp_path / "other"]
    stale = targets[0] / "demo" / "stale.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("old", encoding="utf-8")

    installed = AgentService(AgentSettings(skill_dirs=targets), Progress(), skills_dir=skills_dir).install()

    assert [i.target for i in installed] == [targets[0] / "demo", targets[1] / "demo"]
    assert (targets[1] / "demo" / "references" / "notes.md").is_file()
    assert not stale.exists()


def test_default_skill_dir_is_claude_code():
    assert [str(p) for p in AgentSettings().skill_dirs] == ["~/.claude/skills"]


def test_missing_bundle_is_a_heinzel_error(monkeypatch, tmp_path):
    monkeypatch.setattr(service_module, "files", lambda _: tmp_path)
    monkeypatch.setattr(service_module, "__file__", str(tmp_path / "a" / "b" / "c" / "d" / "e.py"))
    with pytest.raises(HeinzelError, match="not packaged"):
        service_module.bundled_skills_dir()


def test_uninstall_removes_only_heinzel_skills(skills_dir, tmp_path):
    target = tmp_path / "claude"
    service = AgentService(AgentSettings(skill_dirs=[target]), Progress(), skills_dir=skills_dir)
    service.install()
    (target / "someone-else").mkdir()

    assert [skill.name for skill in service.uninstall()] == ["demo"]
    assert sorted(p.name for p in target.iterdir()) == ["someone-else"]
    assert service.uninstall() == []
