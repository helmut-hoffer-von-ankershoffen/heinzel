# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""The ADR index and the ADR pages of the documentation site (ADR 0001, ADR 0082).

Every record's front matter carries `implementation` (built, partial or planned: how much of the decision the code does
today) and `implemented-by` (pull request numbers). This module renders them as the index table, one row per ADR
(scripts/generate_docs.py writes it between the markers below, a test pins it) and, as an MkDocs hook listed in
mkdocs.yml, as a short block under the title of each ADR page. The relations between records (`amends`, `supersedes`
and their reverses) stay in the front matter, where tests check them; the site does not narrate them.
The core repository only: plugins have no ADRs.
"""

import re
from datetime import date
from pathlib import Path
from typing import Any

ADR_FILE = re.compile(r"^(\d{4})-.+\.md$")
ADR_TITLE = re.compile(r"^# \d{4}: (.+)$", re.MULTILINE)
INDEX_START, INDEX_END = "<!-- adr-index:start -->", "<!-- adr-index:end -->"
RELATIONS = ("amends", "amended-by", "supersedes", "superseded-by")


class MissingAdrTargetError(ValueError):
    """An ADR relation points to a record that does not exist."""

    def __init__(self, number: str, relation: str, target: str) -> None:
        """Identify the source, relation and missing target."""
        super().__init__(f"ADR {number} {relation} references missing ADR {target}")


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
    """Every record by number, including its `file` and `title`.

    Raises:
        MissingAdrTargetError: A relation references a missing record.
    """
    records: dict[str, dict[str, str]] = {}
    for path in sorted(directory.iterdir()):
        match = ADR_FILE.match(path.name)
        if not match or match.group(1) == "0000":
            continue
        text = path.read_text(encoding="utf-8")
        title = ADR_TITLE.search(text)
        records[match.group(1)] = {**adr_frontmatter(text), "file": path.name, "title": title.group(1) if title else ""}
    for number, meta in records.items():
        for relation in RELATIONS:
            for target in numbers(meta.get(relation, "")):
                if target not in records:
                    raise MissingAdrTargetError(number, relation, target)
    return records


def pull_links(meta: dict[str, str], repo_url: str) -> list[str]:
    """Links to the pull requests that implement an ADR."""
    return [f"[#{n}]({repo_url}/pull/{n})" for n in numbers(meta.get("implemented-by", ""))]


def _short_date(value: str) -> str:
    return f"{date.fromisoformat(value):%-d %b}"


def adr_index_table(records: dict[str, dict[str, str]], repo_url: str) -> str:
    """The index table: title and number, status and date over implementation, and the pull requests."""
    rows = [
        "| ADR | Status | Pull requests |",
        "| --- | --- | --- |",
    ]
    for number, meta in records.items():
        cells = [
            f"[{number}]({meta['file']}) {meta['title']}",
            f"{meta['status']} · {_short_date(meta['date'])}<br>{meta['implementation']}",
            ", ".join(pull_links(meta, repo_url)),
        ]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def adr_index_block(directory: Path, repo_url: str) -> str:
    """The generated part of docs/adr/README.md, between its two markers."""
    table = adr_index_table(adr_records(directory), repo_url)
    return f'<div class="adr-index" markdown="1">\n\n{table}\n\n</div>'


def adr_page_block(records: dict[str, dict[str, str]], number: str, repo_url: str) -> str:
    """What an ADR page shows under its title: how much runs, and the pull requests that built it."""
    lines = [f"- **Implementation** {records[number]['implementation']}"]
    links = pull_links(records[number], repo_url)
    if links:
        lines.append(f"- **Implemented by** {', '.join(links)}")
    return '<div class="adr-relations" markdown="1">\n\n' + "\n".join(lines) + "\n\n</div>\n\n"


def on_page_markdown(markdown: str, *, page: Any, config: Any, **_: Any) -> str:  # noqa: ANN401 - mkdocs' own objects
    """Show the implementation status and the pull requests of an ADR under its title."""
    repo = str(config.get("repo_url", "")).rstrip("/")
    source = Path(page.file.abs_src_path or "")
    match = ADR_FILE.match(source.name)
    if not repo or not match or source.parent.name != "adr" or match.group(1) == "0000":
        return markdown
    block = adr_page_block(adr_records(source.parent), match.group(1), repo).rstrip("\n")
    return ADR_TITLE.sub(lambda m: f"{m.group(0)}\n\n{block}", markdown, count=1)
