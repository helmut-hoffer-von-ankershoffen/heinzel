# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""MkDocs hooks for the documentation site (ADR 0082); listed under `hooks:` in mkdocs.yml, loaded by mkdocs itself.

The site is built from docs/ only, but documents link to files at the repository root (`../../README.md`,
`../../SECURITY.md`) and to source files, which GitHub renders and the site has no page for. Those links become links
to the file on GitHub, so nothing on the site is broken and the Markdown stays as GitHub needs it.
"""

import re
import stat
from collections.abc import Iterable, Mapping
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
    depth = page.file.src_uri.count("/")

    def replace(match: re.Match[str]) -> str:
        ups = match.group(2).count("../")
        if ups <= depth:  # stays inside docs/
            return match.group(0)
        return f"{match.group(1)}{repo}/blob/main/{match.group(3)}{match.group(4) or ''}{match.group(5)}"

    return LINK.sub(replace, markdown)
