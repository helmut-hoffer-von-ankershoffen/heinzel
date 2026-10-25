"""The documentation module: what each installed plugin brings, as a model and as Markdown pages (ADR 0058).

`hz system plugin docs show` is the model, for people and agents; `hz system plugin docs build` writes the pages a
repository commits (`docs/plugins/<distribution>/index.md`) and checks them in CI. `hz system modules` stays the
one-line listing of modules and their lets; this is the full account.
"""

from pathlib import Path
from typing import Annotated

from pydantic import Field

from heinzel import HeinzelError, Module
from heinzel.modules.system.plugin.docs._build import build_pages
from heinzel.modules.system.plugin.docs._models import BuildResult, Documentation
from heinzel.modules.system.plugin.docs._render import render_documentation
from heinzel.modules.system.plugin.docs._service import DocsService

module = Module("docs", "Document installed plugins: what each brings, as a model and as Markdown pages.")
module.service(DocsService)

# What was installed from where, with its settings' keys and defaults, is yours to see, not any local page's: the
# same heads as `hz system plugin list` (ADR 0019, 0039).
SHOWN = frozenset({"cli", "mcp-stdio", "web"})
WRITES = frozenset({"cli", "mcp-stdio"})

Plugin = Annotated[
    str | None,
    Field(
        description="One plugin, by its short name (`clock`) or distribution (`heinzel-clock`); every one if absent."
    ),
]
DEFAULT_SCENES = Path("docs") / "scenes.toml"
Scenes = Annotated[
    Path,
    Field(
        description="The `docs/scenes.toml` declaring what to record for the plugin (ADR 0058); when the default "
        "doesn't exist, a CLI scene is derived from the operations."
    ),
]
Builtin = Annotated[bool, Field(description="Include Heinzelmännchen's own modules as the page `heinzel`.")]


@module.operation(heads=SHOWN, render=render_documentation)
def show(
    service: DocsService, plugin: Plugin = None, scenes: Scenes = DEFAULT_SCENES, builtin: Builtin = False
) -> Documentation:
    """Show what each installed plugin brings; `--json` is the full model its page is built from.

    Operations in every head, settings, credentials, adapters, themes, playbooks, jobs, layers and scenes, read from
    the kernel: nothing is parsed from source.
    """
    return service.documentation(plugin=plugin, scenes=_scenes(scenes), builtin=builtin)


@module.operation(read_only=False, heads=WRITES)
def build(  # noqa: PLR0913, PLR0917 - what to document, where, and how; all with defaults, options on the CLI
    service: DocsService,
    directory: Annotated[
        Path, Field(description="Where the pages go: one `<distribution>/index.md` each, with an `assets/` beside it.")
    ] = Path("docs/plugins"),
    plugin: Plugin = None,
    scenes: Scenes = DEFAULT_SCENES,
    builtin: Builtin = False,
    index: Annotated[bool, Field(description="Also write `README.md`, a gallery of the pages built.")] = True,
    check: Annotated[
        bool, Field(description="Write nothing; report the pages that differ from what would be written, for CI.")
    ] = False,
) -> BuildResult:
    """Write a Markdown page per installed plugin, with theme swatches and its recordings when they exist.

    Raises:
        HeinzelError: If `plugin` names no installed plugin, the scenes file is invalid, or with `check` a page is
            out of date (so `make check` fails).
    """
    documentation = service.documentation(plugin=plugin, scenes=_scenes(scenes), builtin=builtin, assets=directory)
    result = build_pages(documentation, directory, index=index, check=check)
    if result.stale:
        pages = ", ".join(result.stale_pages)
        msg = f"{len(result.stale_pages)} page(s) out of date in {directory}: {pages}; run `{result.next[0]}`"
        raise HeinzelError(msg)
    return result


def _scenes(path: Path) -> Path | None:
    """The scenes file to read: the default only when it exists, a given one always (so a typo is an error)."""
    return None if path == DEFAULT_SCENES and not path.is_file() else path


__all__ = ["module"]
