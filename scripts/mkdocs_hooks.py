# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""MkDocs hooks for the documentation site (ADR 0082); listed under `hooks:` in mkdocs.yml, loaded by mkdocs itself.

The site is built from docs/ only, but documents link to files at the repository root (`../../README.md`,
`../../SECURITY.md`) and to source files, which GitHub renders and the site has no page for. Those links become links
to the file on GitHub, so nothing on the site is broken and the Markdown stays as GitHub needs it.

The same module renders the ADR relations (`implemented-by`, `amends`, `amends-NNNN`, `supersedes` and their back
links) from the records' front matter: as the index table of docs/adr/README.md (scripts/generate_docs.py writes it,
a test pins it) and as a short block under the title of every ADR page.
"""

import re
import stat
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any, Protocol

LINK = re.compile(r"(\]\()((?:\.\./)+)([^)\s#]+)(#[^)\s]*)?(\))")


class SourceFile(Protocol):
    """The source-path field MkDocs exposes before copying or rendering a file."""

    @property
    def abs_src_path(self) -> str | None:
        """The concrete source path, absent for generated theme files."""
        ...


def on_files[T: Iterable[SourceFile]](files: T, config: Mapping[str, object]) -> T:
    """Reject linked or nonregular repository sources before the site can copy their contents.

    Raises:
        ValueError: If a repository source would follow a link or read a nonregular file.
    """
    root = Path(str(config["docs_dir"])).absolute()
    for source in files:
        if source.abs_src_path is None:
            continue
        path = Path(source.abs_src_path).absolute()
        if not path.is_relative_to(root):
            continue  # Installed theme assets are separate from this repository's frozen docs tree.
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            msg = "The documentation site refuses symlinks in repository sources"
            raise ValueError(msg)
        metadata = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            msg = "The documentation site refuses linked or nonregular repository sources"
            raise ValueError(msg)
    return files


def on_page_markdown(markdown: str, *, page: Any, config: Any, **_: Any) -> str:  # noqa: ANN401 - mkdocs' own objects
    """Links that leave docs/ point at the file on GitHub, at the branch the site is built from."""
    repo = str(config.get("repo_url", "")).rstrip("/")
    if not repo:
        return markdown
    source = Path(page.file.abs_src_path or "")
    match = ADR_FILE.match(source.name)
    if match and source.parent.name == "adr" and match.group(1) != "0000":
        records = adr_records(source.parent)
        block = adr_page_block(records, match.group(1), repo)
        markdown = (
            ADR_TITLE.sub(lambda m: m.group(0) + "\n\n" + block.rstrip("\n"), markdown, count=1) if block else markdown
        )
    depth = page.file.src_uri.count("/")

    def replace(match: re.Match[str]) -> str:
        ups = match.group(2).count("../")
        if ups <= depth:  # stays inside docs/
            return match.group(0)
        return f"{match.group(1)}{repo}/blob/main/{match.group(3)}{match.group(4) or ''}{match.group(5)}"

    return LINK.sub(replace, markdown)


ADR_FILE = re.compile(r"^(\d{4})-.+\.md$")
ADR_TITLE = re.compile(r"^# \d{4}: (.+)$", re.MULTILINE)
INDEX_START, INDEX_END = "<!-- adr-index:start -->", "<!-- adr-index:end -->"
FORWARD = {"amends": "amended-by", "supersedes": "superseded-by"}
LABELS = {
    "amends": "amends",
    "amended-by": "amended by",
    "supersedes": "supersedes",
    "superseded-by": "superseded by",
}


def numbers(value: str) -> list[str]:
    """The ADR numbers of a front matter value such as `0010, 0020`."""
    return [n.strip() for n in value.split(",") if n.strip()]


def adr_frontmatter(text: str) -> dict[str, str]:
    """The flat `key: value` front matter of an ADR."""
    lines = text.splitlines()
    fields: dict[str, str] = {}
    if not lines or lines[0].strip() != "---":
        return fields
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def adr_records(directory: Path) -> dict[str, dict[str, str]]:
    """Every record of an ADR directory by number: its front matter plus `file` and `title`."""
    records: dict[str, dict[str, str]] = {}
    for path in sorted(directory.iterdir()):
        match = ADR_FILE.match(path.name)
        if not match or match.group(1) == "0000":
            continue
        text = path.read_text(encoding="utf-8")
        title = ADR_TITLE.search(text)
        records[match.group(1)] = {**adr_frontmatter(text), "file": path.name, "title": title.group(1) if title else ""}
    return records


def adr_relations(records: dict[str, dict[str, str]], number: str) -> dict[str, list[tuple[str, str]]]:
    """The relations of one ADR as (other number, reason) pairs; the reason is what the amending ADR says it changed."""
    meta = records[number]
    relations: dict[str, list[tuple[str, str]]] = {}
    for forward, backward in FORWARD.items():
        relations[forward] = [(n, meta.get(f"{forward}-{n}", "")) for n in numbers(meta.get(forward, ""))]
        relations[backward] = [(n, records[n].get(f"{forward}-{number}", "")) for n in numbers(meta.get(backward, ""))]
    return relations


def pull_links(meta: dict[str, str], repo_url: str) -> list[str]:
    """Links to the pull requests that implement an ADR."""
    return [f"[#{n}]({repo_url}/pull/{n})" for n in numbers(meta.get("implemented-by", ""))]


def _short_date(value: str) -> str:
    return f"{date.fromisoformat(value):%-d %b}"


def _link(records: dict[str, dict[str, str]], other: str, reason: str) -> str:
    title = f' "{reason}"' if reason else ""
    return f"[{other}]({records[other]['file']}{title})"


def adr_index_table(records: dict[str, dict[str, str]], repo_url: str) -> str:
    """The index table: title and number, status over date, the pull requests and the amendments, one row per ADR."""
    rows = [
        "| ADR | Status | Pull requests | Amendments |",
        "| --- | --- | --- | --- |",
    ]
    for number, meta in records.items():
        relations = adr_relations(records, number)
        amendments = [
            f"{LABELS[kind]} " + ", ".join(_link(records, n, reason) for n, reason in pairs)
            for kind, pairs in relations.items()
            if pairs
        ]
        cells = [
            f"[{number}]({meta['file']}) {meta['title']}",
            f"{meta['status']} · {_short_date(meta['date'])}",
            ", ".join(pull_links(meta, repo_url)),
            "<br>".join(amendments),
        ]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def adr_index_block(directory: Path, repo_url: str) -> str:
    """The generated part of docs/adr/README.md, between its two markers."""
    table = adr_index_table(adr_records(directory), repo_url)
    return f'<div class="adr-index" markdown="1">\n\n{table}\n\n</div>'


def adr_page_block(records: dict[str, dict[str, str]], number: str, repo_url: str) -> str:
    """What an ADR page shows under its title: the pull requests and each relation with the reason it gives."""
    lines = []
    links = pull_links(records[number], repo_url)
    if links:
        lines.append(f"- **Implemented by** {', '.join(links)}")
    for kind, pairs in adr_relations(records, number).items():
        if pairs:
            items = ", ".join(_link(records, n, "") + (f" ({reason})" if reason else "") for n, reason in pairs)
            lines.append(f"- **{LABELS[kind].capitalize()}** {items}")
    return '<div class="adr-relations" markdown="1">\n\n' + "\n".join(lines) + "\n\n</div>\n\n" if lines else ""
