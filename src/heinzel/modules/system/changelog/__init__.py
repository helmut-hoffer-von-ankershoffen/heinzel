"""The changelog submodule of system: release notes of the installed version."""

from importlib.resources import files
from pathlib import Path

from markdown_it import MarkdownIt
from rich.markdown import Markdown

from heinzel import HeinzelError, Module

FILENAME = "CHANGELOG.md"

module = Module("changelog", "Release notes of the installed version.")


def as_markdown(text: str) -> Markdown:
    """Render the changelog as Markdown, showing text like `heinzel-<name>` verbatim.

    Rich's parser reads `<name>` as inline HTML and drops it; the changelog contains no HTML, so turn HTML off.
    """
    markdown = Markdown(text)
    markdown.parsed = MarkdownIt("commonmark", {"html": False}).enable("strikethrough").enable("table").parse(text)
    return markdown


@module.operation(render=as_markdown)
def changelog() -> str:
    """Show the changelog of the installed version, as Markdown.

    Raises:
        HeinzelError: If the changelog is not part of this installation.
    """
    packaged = files("heinzel").joinpath(FILENAME)
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    # Editable installs don't contain the force-included copy; read it from the repository root instead.
    source_tree = Path(__file__).resolve().parents[5] / FILENAME
    if source_tree.is_file():
        return source_tree.read_text(encoding="utf-8")
    msg = f"{FILENAME} is not packaged with this installation of Heinzelmännchen."
    raise HeinzelError(msg)
