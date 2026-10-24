"""Documentation can't drift: generated parts are checked by `make lint`, the rest here."""

import inspect
import re
import tomllib
from pathlib import Path
from typing import Annotated, get_args, get_origin
from urllib.parse import quote

import heinzel_atlassian
import heinzel_clock
import heinzel_github
import heinzel_zoom
import pytest
from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo

from heinzel import OperationLet, SettingsLet
from heinzel.heads._exposure import exposed
from heinzel.modules.system.agent._service import parse_frontmatter

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
MARKDOWN = sorted(
    p
    for p in REPO.rglob("*.md")
    if not {".venv", "node_modules", ".pytest_cache", "worktrees"} & set(p.relative_to(REPO).parts) and p.is_file()
)
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
# Links to GitHub pages of the repository, not to files in it. GitHub resolves them against the document's URL
# (`<owner>/<repo>/blob/<ref>/SECURITY.md`), so from a root document they work in every repository, plugins included.
GITHUB_ROUTES = {"../../security/advisories/new": "private vulnerability reporting, linked from SECURITY.md"}


def _relative_links(path: Path) -> list[str]:
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    text = re.sub(r"`[^`\n]*`", "", text)  # links shown as code are examples
    return [t for t in LINK.findall(text) if not re.match(r"^[a-z]+:|^#|^mailto:", t)]


@pytest.mark.parametrize("document", MARKDOWN, ids=lambda p: str(p.relative_to(REPO)))
def test_relative_links_resolve(document):
    def exists(target: str) -> bool:
        path = document.parent / target.split("#", maxsplit=1)[0]
        return path.exists() or path.with_name(path.name + ".jinja").exists()  # template files render to their name

    links = [target for target in _relative_links(document) if "[[" not in target and target not in GITHUB_ROUTES]
    broken = [target for target in links if not exists(target)]
    assert not broken, f"{document.relative_to(REPO)} links to missing files: {broken}"


@pytest.mark.parametrize("root", [REPO, REPO / "template"], ids=["heinzel", "template"])
def test_security_policy_links_to_private_vulnerability_reporting(root):
    """Reporters land on the advisory form of the repository they read the policy in; OpenSSF Scorecard also wants
    an absolute link, which GitHub's guide for reporters is."""
    policy = next(root / name for name in ("SECURITY.md", "SECURITY.md.jinja") if (root / name).exists())
    policy = policy.read_text(encoding="utf-8")
    assert "](../../security/advisories/new)" in policy
    assert re.search(r"\]\(https://docs\.github\.com/[^)]*report-privately\)", policy)


BADGES = re.compile(r"<!-- badges:start -->\n(.*?)<!-- badges:end -->", re.DOTALL)
BADGE = re.compile(r"^\[!\[[^\]]+\]\([^)\s]+\)\]\([^)\s]+\)$")
# Every top-level page GitHub renders, except LICENSE (raw text), AGENTS.md and CLAUDE.md (agent instructions) and
# CHANGELOG.md (the release process writes it). scripts/generate_docs.py writes the block; this pins the result.
BADGE_PAGES = ["README.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "INTENT.md"]
TEMPLATE_BADGE_PAGES = ["README.md.jinja", "SECURITY.md.jinja"]


def badge_block(path: Path) -> str:
    """The badges between a page's markers."""
    match = BADGES.search(path.read_text(encoding="utf-8"))
    assert match, f"{path.name} has no badge block"
    return match[1]


def test_finds_the_badge_block(tmp_path):
    page = tmp_path / "x.md"
    page.write_text("# X\n\n<!-- badges:start -->\n[![a](b)](c)\n<!-- badges:end -->\n", encoding="utf-8")
    assert badge_block(page) == "[![a](b)](c)\n"
    assert BADGE.match("[![a](b)](c)")
    assert not BADGE.match("![a](b)")


def test_every_page_shows_the_same_linked_badges_at_most_ten():
    """One badge set, so what README promises CONTRIBUTING and SECURITY promise too; each badge leads somewhere."""
    blocks = {page: badge_block(REPO / page) for page in BADGE_PAGES}
    assert len(set(blocks.values())) == 1, "run `make fix`"
    badges = blocks["README.md"].splitlines()
    assert 1 <= len(badges) <= 10
    assert [badge for badge in badges if not BADGE.match(badge)] == []
    outside = {p.name for p in REPO.glob("*.md") if "[![" in BADGES.sub("", p.read_text(encoding="utf-8"))}
    assert outside == set(), "badges belong in the generated block"
    assert "badge" not in (REPO / "LICENSE").read_text(encoding="utf-8")


def test_python_badge_lists_the_versions_pyproject_declares():
    classifiers = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["classifiers"]
    versions = [c.rsplit(" :: ", 1)[1] for c in classifiers if c.startswith("Programming Language :: Python :: 3.")]
    expected = "python-" + quote(" | ".join(versions)) + "-blue"
    assert expected in badge_block(REPO / "README.md")


@pytest.mark.parametrize("page", TEMPLATE_BADGE_PAGES)
def test_plugins_show_the_same_badges_for_their_own_repository(page):
    ours = badge_block(REPO / "README.md").replace("helmut-hoffer-von-ankershoffen/heinzel", "OWNER/REPO")
    theirs = badge_block(REPO / "template" / page).replace("[[ github_owner ]]/[[ project_name ]]", "OWNER/REPO")
    assert theirs == ours


def test_every_document_in_docs_is_listed_in_the_index():
    index = (REPO / "docs" / "README.md").read_text(encoding="utf-8") + (REPO / "docs" / "adr" / "README.md").read_text(
        encoding="utf-8"
    )
    unlisted = [
        p.relative_to(REPO / "docs").as_posix()
        for p in (REPO / "docs").rglob("*.md")
        if p.name != "README.md" and p.name not in index and p.relative_to(REPO / "docs").as_posix() not in index
    ]
    assert not unlisted


ADRS = sorted(
    [*REPO.glob("docs/adr/[0-9][0-9][0-9][0-9]-*.md"), *REPO.glob("template/docs/adr/[0-9][0-9][0-9][0-9]-*.md")]
)
ADRS = [adr for adr in ADRS if not adr.name.startswith("0000-")]
ADR_STATUSES = {"proposed", "accepted", "rejected", "deprecated", "superseded"}
ADR_SECTIONS = [  # docs/adr/0000-template.md; optional ones may sit between these, nothing else may
    "## Context and Problem Statement",
    "## Considered Options",
    "## Decision Outcome",
    "### Consequences",
    "### Confirmation",
]
ADR_OPTIONAL_SECTIONS = {"## Decision Drivers", "## More Information"}
ADR_LINKS = {"supersedes": "superseded-by", "amends": "amended-by"}


def _adr_numbers(value: str) -> set[str]:
    return {n.strip() for n in value.split(",") if n.strip()}


@pytest.mark.parametrize("adr", ADRS, ids=lambda p: str(p.relative_to(REPO)))
def test_adrs_follow_the_template(adr):
    text = adr.read_text(encoding="utf-8")
    meta = parse_frontmatter(text)
    number = adr.name[:4]
    assert meta.get("status") in ADR_STATUSES, "front matter needs a status from the template"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", meta.get("date", "")), "front matter needs date: YYYY-MM-DD"
    assert (meta["status"] == "superseded") == ("superseded-by" in meta), "superseded goes with superseded-by"
    assert re.search(rf"^# {number}: \S", text, flags=re.MULTILINE), f"the title must be '# {number}: ...'"
    headings = re.findall(r"^#{2,3} .+$", text, flags=re.MULTILINE)
    assert [h for h in headings if h in ADR_SECTIONS] == ADR_SECTIONS, "required sections, in template order"
    assert set(headings) <= {*ADR_SECTIONS, *ADR_OPTIONAL_SECTIONS}, "only the template's sections"


@pytest.mark.parametrize("directory", sorted({adr.parent for adr in ADRS}), ids=lambda p: str(p.relative_to(REPO)))
def test_adr_index_and_links_match_the_records(directory):
    index = (directory / "README.md").read_text(encoding="utf-8")
    records = {adr.name[:4]: (adr, parse_frontmatter(adr.read_text(encoding="utf-8"))) for adr in ADRS}
    records = {number: record for number, record in records.items() if record[0].parent == directory}
    for number, (adr, meta) in records.items():
        title = re.search(r"^# \d{4}: (.+)$", adr.read_text(encoding="utf-8"), flags=re.MULTILINE)
        assert title
        row = f"| [{number}]({adr.name}) | {title.group(1)} | {meta['status']} | {meta['date']} |"
        assert row in index, f"{directory.relative_to(REPO)}/README.md needs the row: {row}"
        assert _adr_numbers(meta.get("related", "")) <= records.keys(), f"{number} relates to a missing ADR"
        for forward, backward in ADR_LINKS.items():
            for other in _adr_numbers(meta.get(forward, "")):
                assert number in _adr_numbers(records[other][1].get(backward, "")), (
                    f"{other} lacks {backward}: {number}"
                )
            for other in _adr_numbers(meta.get(backward, "")):
                assert number in _adr_numbers(records[other][1].get(forward, "")), f"{other} lacks {forward}: {number}"


def test_every_parameter_setting_and_module_is_described(make_kernel):
    kernel = make_kernel(
        builtins=True,
        atlassian=heinzel_atlassian.module,
        clock=heinzel_clock.module,
        github=heinzel_github.module,
        zoom=heinzel_zoom.module,
    )  # first-party plugins too
    missing = [r.qualname for r in kernel.modules if not r.module.summary]
    for item in exposed(kernel, "cli"):
        for parameter in item.parameters:
            metadata = get_args(parameter.annotation)[1:] if get_origin(parameter.annotation) is Annotated else ()
            if not any(isinstance(m, FieldInfo) and m.description for m in metadata):
                missing.append(f"{' '.join(item.path)} {parameter.name}")
    for record, let in kernel.lets(SettingsLet):
        missing += [f"[{record.qualname}] {k}" for k, f in let.model.model_fields.items() if not f.description]
    for _, operation in kernel.lets(OperationLet):
        if not inspect.getdoc(operation.func):
            missing.append(operation.name)
        missing += [
            f"{operation.name} -> {field}" for field in undescribed_fields(operation.signature().return_annotation)
        ]
    assert not missing, f"Undescribed (help text in every head, MCP and OpenAPI output schemas): {missing}"


def undescribed_fields(annotation: object, seen: set[type] | None = None) -> list[str]:
    """`Model.field` for every field without a description in the models an annotation returns, nested ones too."""
    seen = set() if seen is None else seen
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return []
        seen.add(annotation)
        missing = [f"{annotation.__name__}.{name}" for name, f in annotation.model_fields.items() if not f.description]
        for f in annotation.model_fields.values():
            missing += undescribed_fields(f.annotation, seen)
        return missing
    return [field for arg in get_args(annotation) for field in undescribed_fields(arg, seen)]


def test_finds_undescribed_result_fields():
    class Inner(BaseModel):
        bare: int
        described: int = Field(description="Has one.")

    class Outer(BaseModel):
        inner: list[Inner] = Field(description="Nested.")
        again: dict[str, Inner | None] = Field(description="Seen before.")

    assert undescribed_fields(list[Outer]) == ["Inner.bare"]


SUMMARY_LIMIT = 100
PLURAL_MODULE_NAMES: set[str] = set()  # module names that are rightly plural; none yet


def summary_problems(summary: str) -> list[str]:
    """Why a summary isn't one sentence ending in `.`, at most SUMMARY_LIMIT characters."""
    problems = []
    if len(summary) > SUMMARY_LIMIT:
        problems.append(f"{len(summary)} > {SUMMARY_LIMIT} characters")
    if not summary.endswith(".") or re.search(r"[.!?]\s", summary):
        problems.append("not one sentence ending in '.'")
    return problems


def test_finds_bad_summaries():
    assert summary_problems("List plugins.") == []
    assert summary_problems("List plugins") == ["not one sentence ending in '.'"]
    assert summary_problems("List plugins. Then more.") == ["not one sentence ending in '.'"]
    assert summary_problems("x" * 100 + ".") == ["101 > 100 characters"]


def test_operation_summaries_are_one_short_sentence_and_module_names_singular(make_kernel):
    """Summaries are the help line, the MCP tool description and the OpenAPI summary; they must read well in all."""
    kernel = make_kernel(
        builtins=True,
        atlassian=heinzel_atlassian.module,
        clock=heinzel_clock.module,
        github=heinzel_github.module,
        zoom=heinzel_zoom.module,
    )  # first-party plugins too
    problems = [
        f"{record.qualname} {operation.name}: {problem}"
        for record, operation in kernel.lets(OperationLet)
        for problem in summary_problems(operation.summary)
    ]
    problems += [
        f"module {r.qualname}: plural name"
        for r in kernel.modules
        if r.module.name.endswith("s") and r.module.name not in PLURAL_MODULE_NAMES
    ]
    assert not problems
