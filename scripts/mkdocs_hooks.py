# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""MkDocs hooks for the documentation site (ADR 0058); listed under `hooks:` in mkdocs.yml, loaded by mkdocs itself.

The site is built from docs/ only, but documents link to files at the repository root (`../../README.md`,
`../../SECURITY.md`) and to source files, which GitHub renders and the site has no page for. Those links become links
to the file on GitHub, so nothing on the site is broken and the Markdown stays as GitHub needs it.
"""

import re
from typing import Any

LINK = re.compile(r"(\]\()((?:\.\./)+)([^)\s#]+)(#[^)\s]*)?(\))")


def on_page_markdown(markdown: str, *, page: Any, config: Any, **_: Any) -> str:  # noqa: ANN401 - mkdocs' own objects
    """Links that leave docs/ point at the file on GitHub, at the branch the site is built from."""
    repo = str(config.get("repo_url", "")).rstrip("/")
    if not repo:
        return markdown
    depth = page.file.src_uri.count("/")

    def replace(match: re.Match[str]) -> str:
        ups = match.group(2).count("../")
        if ups <= depth:  # stays inside docs/
            return match.group(0)
        return f"{match.group(1)}{repo}/blob/main/{match.group(3)}{match.group(4) or ''}{match.group(5)}"

    return LINK.sub(replace, markdown)
