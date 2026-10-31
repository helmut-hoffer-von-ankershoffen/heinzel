"""The product's name, tagline, descriptor and claims agree across README, PyPI text, site, manifest, CLI and app."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
DESCRIPTION = (
    "Heinzelmännchen is an open-source crew of AI helpers that drafts your team's busywork "
    "and asks before anything leaves your machine."
)
TAIL = DESCRIPTION.removeprefix("Heinzelmännchen is an ")  # fields that already name the project drop the subject
TAGLINE = "Less busywork. More good work."
DESCRIPTOR = "helpers accelerating product teams"


def read(name: str) -> str:
    return (ROOT / name).read_text()


@pytest.mark.parametrize(
    "name",
    ["README.md", "PYPI.md", "docs/README.md", ".claude-plugin/marketplace.json", "src/heinzel/heads/mcp/__init__.py"],
)
def test_the_description_sentence_appears_verbatim(name: str):
    assert DESCRIPTION in " ".join(read(name).split()).replace('" "', "")


@pytest.mark.parametrize("name", ["pyproject.toml", "mkdocs.yml"])
def test_fields_that_name_the_project_carry_the_rest_of_the_sentence(name: str):
    assert TAIL in read(name)


def test_tagline_is_the_hero_of_pypi_and_the_home_page():
    assert TAGLINE in read("PYPI.md")
    assert "Less busywork.<br>More good work." in read("docs/README.md")


def test_descriptor_is_the_manifest_description_and_the_cli_tagline():
    assert f'"description": "{DESCRIPTOR}"' in read("web/public/manifest.webmanifest")
    assert f'TAGLINE = "{DESCRIPTOR}"' in read("src/heinzel/heads/cli/_branding.py")


@pytest.mark.parametrize("name", ["README.md", "PYPI.md", "docs/README.md", "docs/CATALOG.md", "docs/VISION.md"])
def test_heinzel_is_never_the_product_name_and_the_old_description_is_retired(name: str):
    text = read(name)
    for phrase in (
        "Install Heinzel ",
        "Try Heinzel ",
        "the Heinzel workspace",
        "Make Heinzel yours",
        "AI-driven operating system",
    ):
        assert phrase not in text, phrase


def test_acme_is_written_in_capitals_in_user_facing_docs():
    for path in [*(ROOT / "docs").glob("*.md"), *(ROOT / "docs/guides").glob("*.md")]:
        assert not re.search(r"\bAcme\b", path.read_text()), path.name
    assert not re.search(r"\bAcme\b", read("web/src/i18n.ts"))


def test_the_ascii_name_never_loses_its_double_e():
    roots = [ROOT / "src", ROOT / "web/src", ROOT / "docs"]
    files = [p for r in roots for p in r.rglob("*") if p.suffix in {".py", ".ts", ".tsx", ".md"} and p.is_file()]
    files += [ROOT / "README.md", ROOT / "PYPI.md"]
    assert [p for p in files if "Heinzelmannchen" in p.read_text()] == []


def test_hero_and_description_make_no_claims_we_cannot_show():
    banned = re.compile(r"autonomous|automatically|while you sleep", re.IGNORECASE)
    readme_head = "\n".join(read("README.md").splitlines()[:60])
    mkdocs = re.search(r"^site_description: (.*)$", read("mkdocs.yml"), re.MULTILINE)
    assert mkdocs
    for text in (readme_head, read("PYPI.md"), read("docs/README.md"), mkdocs.group(1)):
        assert not banned.search(text), banned.search(text)


def test_branding_page_is_short_and_listed():
    page = read("docs/BRANDING.md")
    assert len(page.splitlines()) <= 60
    assert "BRANDING.md" in read("docs/README.md")
