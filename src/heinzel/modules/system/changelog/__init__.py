"""The changelog submodule of system: release notes of the installed version, and of the releases newer than it."""

import urllib.error
from importlib.resources import files
from pathlib import Path
from typing import Annotated

from markdown_it import MarkdownIt
from packaging.version import InvalidVersion, Version
from pydantic import Field
from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.rule import Rule
from rich.text import Text

from heinzel import HeinzelError, Module, __version__
from heinzel.kernel import Installation
from heinzel.modules.system._models import ReleaseNotes
from heinzel.modules.system._service import RELEASE_TIMEOUT, clean, github_releases

FILENAME = "CHANGELOG.md"

module = Module("changelog", "Release notes of the installed version and of newer releases.")


def as_markdown(text: str, *, hyperlinks: bool = True) -> Markdown:
    """Render the changelog as Markdown, showing text like `heinzel-<name>` verbatim.

    Rich's parser reads `<name>` as inline HTML and drops it; the changelog contains no HTML, so turn HTML off.
    Third-party notes get `hyperlinks=False`: a link shows its target next to its text instead of hiding it.
    """
    markdown = Markdown(text, hyperlinks=hyperlinks)
    markdown.parsed = MarkdownIt("commonmark", {"html": False}).enable("strikethrough").enable("table").parse(text)
    return markdown


def render(result: str | list[ReleaseNotes]) -> RenderableType:
    """The installed changelog, or each newer release under a heading with its date and page."""
    if isinstance(result, str):
        return as_markdown(result)
    if not result:
        return Text(f"hz {__version__} is the latest release; nothing newer to show.")
    parts: list[RenderableType] = [
        Text(
            f"{len(result)} {'release is' if len(result) == 1 else 'releases are'} newer than the installed "
            f"hz {__version__}, newest first:",
            style="bold",
        )
    ]
    for release in result:
        title = f"hz {release.version}" + (f" ({release.date.isoformat()})" if release.date else "")
        parts.append(Rule(Text(title, style="bold"), align="left"))
        if release.url:
            parts.append(Text(release.url, style="dim"))
        parts.append(as_markdown(release.notes or "_No release notes._", hyperlinks=False))
    parts.append(Text("Update with: hz system update"))
    return Group(*parts)


@module.operation(render=render)
def changelog(
    installation: Installation,
    newer: Annotated[
        bool,
        Field(
            description="Show what the releases newer than the installed version bring instead, newest first; "
            "asks GitHub, anonymously. As JSON: a list of {version, date, notes, url}."
        ),
    ] = False,
) -> str | list[ReleaseNotes]:
    """Show the installed version's changelog as Markdown, or with `--newer` what newer releases bring.

    Only `--newer` uses the network: unauthenticated GETs to the GitHub releases API of the repository in the uv tool
    receipt (or the default repository) with only a `heinzel/<version>` User-Agent (ADR 0031), a few pages at most.

    Raises:
        HeinzelError: If the changelog is not part of this installation, or, with `--newer`, GitHub can't be asked.
    """
    if newer:
        return _newer(installation)
    packaged = files("heinzel").joinpath(FILENAME)
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    # Editable installs don't contain the force-included copy; read it from the repository root instead.
    source_tree = Path(__file__).resolve().parents[5] / FILENAME
    if source_tree.is_file():
        return source_tree.read_text(encoding="utf-8")
    msg = f"{FILENAME} is not packaged with this installation of Heinzelmännchen."
    raise HeinzelError(msg)


def _newer(installation: Installation) -> list[ReleaseNotes]:
    try:
        Version(__version__)
    except InvalidVersion as exc:
        msg = f"hz {__version__} is no release version, so no release is newer or older than it."
        raise HeinzelError(msg) from exc
    releases = github_releases(installation)
    try:
        return releases.newer_than(__version__, RELEASE_TIMEOUT)
    # Offline, rate-limited or an odd answer: one clear error, never a traceback.
    except (OSError, ValueError, TypeError) as exc:
        # An HTTPError reads best whole (it carries the status, e.g. 403 when rate-limited); others by reason.
        reason: object = exc
        if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
            reason = exc.reason
        reason = clean(str(reason))
        msg = (
            f"Could not ask GitHub for the releases newer than hz {__version__} ({reason}). Try again when online, "
            f"or read them on https://github.com/{releases.repository}/releases."
        )
        raise HeinzelError(msg) from exc
