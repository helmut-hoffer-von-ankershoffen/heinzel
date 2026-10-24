"""ATTRIBUTIONS.md lists every package hz ships, deterministically, and `--check` notices when it is stale."""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "attributions.py"
spec = importlib.util.spec_from_file_location("attributions", SCRIPT)
assert spec
assert spec.loader
attributions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(attributions)

EXPORTED = """\
anyio==4.15.1
colorama==0.4.6 ; sys_platform == 'win32'
jeepney==0.9.0 ; sys_platform == 'linux'
Typing_Extensions==4.16.0 ; python_version >= '3.14'
"""


def package(name: str, version: str = "1.0", texts: tuple[tuple[str, str], ...] = ()) -> object:
    return attributions.Package(name, version, "MIT", f"https://example.org/{name}", texts)


def test_python_dependencies_are_those_of_the_platforms_and_pythons_hz_runs_on():
    needed = attributions.locked_requirements(EXPORTED)
    names = {platform: [r.name for r in requirements] for platform, requirements in needed.items()}
    assert names == {
        "aarch64-apple-darwin": ["anyio", "Typing_Extensions"],
        "x86_64-manylinux_2_28": ["anyio", "jeepney", "Typing_Extensions"],
    }
    assert attributions.python_titles(EXPORTED) == {"anyio 4.15.1", "jeepney 0.9.0", "typing-extensions 4.16.0"}


def test_a_license_is_the_spdx_expression_first_then_the_classifiers_then_a_short_license_field():
    assert (
        attributions.spdx({"license-expression": ["MIT"], "classifier": ["License :: OSI Approved :: BSD License"]})
        == "MIT"
    )
    classifiers = ["License :: OSI Approved :: BSD License", "License :: OSI Approved :: Apache Software License"]
    assert attributions.spdx({"classifier": classifiers}) == "Apache-2.0 OR BSD"
    assert attributions.spdx({"license": ["ISC"]}) == "ISC"
    assert attributions.spdx({"license": ["Permission is hereby granted\n..."]}) == "see the license text"


def test_the_url_is_the_source_repository_before_the_home_page():
    metadata = {"project-url": ["Documentation, https://docs.x", "Source, https://github.com/x/x"]}
    assert attributions.python_url("x", metadata) == "https://github.com/x/x"
    assert attributions.python_url("x", {"home-page": ["https://x.org"]}) == "https://x.org"
    assert attributions.python_url("x", {}) == "https://pypi.org/project/x/"


@pytest.mark.parametrize(
    ("repository", "url"),
    [
        ({"url": "git+https://github.com/a/b.git"}, "https://github.com/a/b"),
        ("github:a/b", "https://github.com/a/b"),
        ("a/b", "https://github.com/a/b"),
        ({"type": "git", "url": "git@github.com:a/b.git"}, "https://github.com/a/b"),
    ],
)
def test_an_npm_repository_becomes_a_browsable_url(repository, url):
    assert attributions.repository_url({"repository": repository}) == url


def test_a_license_text_can_not_end_its_code_block_and_is_normalised():
    text = "Use ```this``` freely.  \r\n\r\n"
    rendered = attributions.render([package("x", texts=(("LICENSE", text),))], [])
    assert "````text\nUse ```this``` freely.\n````" in rendered


def test_the_output_is_stable_and_check_reads_back_what_it_lists():
    python = [package("anyio", "4.15.1")]
    web = [package("@xyflow/react", "12.12.0"), package("react", "19.3.1")]
    rendered = attributions.render(python, web)
    assert rendered == attributions.render(python, web)
    assert "| [@xyflow/react](#xyflowreact-12120) | 12.12.0 | MIT |" in rendered
    assert "No license file ships with this package" in rendered
    expected = {
        attributions.PYTHON_SECTION: {"anyio 4.15.1"},
        attributions.WEB_SECTION: {"@xyflow/react 12.12.0", "react 19.3.1"},
    }
    assert attributions.differences(rendered, expected) == []
    expected[attributions.WEB_SECTION] = {"react 19.4.0", "@xyflow/react 12.12.0"}
    assert attributions.differences(rendered, expected) == [
        "Web app (JavaScript) packages: missing react 19.4.0",
        "Web app (JavaScript) packages: no longer shipped react 19.3.1",
    ]


def test_headings_inside_a_license_text_are_not_packages_or_parts():
    epl = "# Eclipse Public License\n\n## Web app (JavaScript) packages\n\n### 1. DEFINITIONS\n"
    rendered = attributions.render([package("x", texts=(("LICENSE.md", epl),))], [package("react", "19.3.1")])
    assert attributions.listed(rendered) == {
        attributions.PYTHON_SECTION: {"x 1.0"},
        attributions.WEB_SECTION: {"react 19.3.1"},
    }


def test_headings_that_github_would_give_the_same_anchor_are_numbered():
    seen: dict[str, int] = {}
    assert [attributions.anchor("a 1.0", seen), attributions.anchor("a 1.0", seen)] == ["a-10", "a-10-1"]


def test_the_web_listing_has_one_entry_per_installed_version(tmp_path):
    for version in ("1.0.0", "2.0.0"):
        folder = tmp_path / version
        folder.mkdir()
        (folder / "package.json").write_text('{"repository": "a/b"}', encoding="utf-8")
        (folder / "LICENSE.md").write_text(f"MIT {version}", encoding="utf-8")
        (folder / "index.js").write_text("", encoding="utf-8")
    listing = {
        "MIT": [
            {"name": "b", "versions": ["1.0.0", "2.0.0"], "paths": [str(tmp_path / "1.0.0"), str(tmp_path / "2.0.0")]}
        ]
    }
    packages = attributions.web_packages(listing)
    assert [(p.title, p.url, p.texts) for p in packages] == [
        ("b 1.0.0", "https://github.com/a/b", (("LICENSE.md", "MIT 1.0.0"),)),
        ("b 2.0.0", "https://github.com/a/b", (("LICENSE.md", "MIT 2.0.0"),)),
    ]
    assert attributions.web_titles(listing) == {"b 1.0.0", "b 2.0.0"}


def test_attributions_md_reads_back_one_heading_per_row_of_its_tables():
    text = (REPO / "ATTRIBUTIONS.md").read_text(encoding="utf-8")
    listed = attributions.listed(text)
    rows = [line for line in text.splitlines() if line.startswith("| [")]
    assert sum(len(titles) for titles in listed.values()) == len(rows)
    assert "typer" in {title.split()[0] for title in listed[attributions.PYTHON_SECTION]}
    assert "react" in {title.split()[0] for title in listed[attributions.WEB_SECTION]}
