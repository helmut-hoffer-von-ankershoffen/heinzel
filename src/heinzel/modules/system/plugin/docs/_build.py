"""Write the pages: one directory per distribution with its `index.md`, swatches and recordings (ADR 0058)."""

from pathlib import Path

from heinzel.modules.system.plugin.docs._models import BuildResult, Documentation
from heinzel.modules.system.plugin.docs._render import gallery, page, swatches
from heinzel.modules.system.plugin.docs._service import ASSETS

INDEX = "README.md"
"""The gallery's file name: GitHub shows it when the directory is opened, MkDocs serves it as the index."""


def rendered(documentation: Documentation, *, index: bool) -> dict[str, str]:
    """Every file a build writes, by path relative to the directory, with its text."""
    files: dict[str, str] = {}
    for doc in documentation.pages:
        files[f"{doc.distribution}/index.md"] = page(doc)
        for theme in doc.themes:
            files[f"{doc.distribution}/{ASSETS}/theme-{theme.name}.svg"] = swatches(theme)
    if index:
        files[INDEX] = gallery(documentation.pages)
    return files


def build_pages(documentation: Documentation, directory: Path, *, index: bool, check: bool) -> BuildResult:
    """Write the files that differ, or with `check` only report them (a page that doesn't exist yet is stale too)."""
    files = rendered(documentation, index=index)
    stale: list[str] = []
    unchanged = 0
    for relative, text in files.items():
        path = directory / relative
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            unchanged += 1
            continue
        stale.append(relative)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    next_steps = [f"hz system plugin docs build --directory {directory}"] if check and stale else []
    unrecorded = [
        f"{doc.distribution}: {scene.name}"
        for doc in documentation.pages
        for scene, recording in zip(doc.scenes, doc.recordings, strict=True)
        if recording.animation is None and scene.kind == "cli"
    ]
    if unrecorded and not check:
        next_steps.append("make docs-record")
    return BuildResult(
        directory=str(directory),
        written=[] if check else stale,
        stale_pages=stale if check else [],
        unchanged=unchanged,
        stale=check and bool(stale),
        next=next_steps,
    )
