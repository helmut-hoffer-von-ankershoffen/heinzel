"""Generate every document that can be derived from the code. `--check` fails if any of them is out of date.

- docs/MODULES.md: every module with its operations in every head (CLI, MCP, REST, web), settings and health checks.
- docs/CLI_REFERENCE.md: Typer's own `utils docs` output for the CLI built from the built-in modules.
- docs/CONFIGURATION.md: every setting of the kernel and the built-in modules, with environment variable and default.
- README.md: the module list between `<!-- modules:start -->` and `<!-- modules:end -->`.
- The badges between `<!-- badges:start -->` and `<!-- badges:end -->`: one set on every top-level page GitHub renders
  (BADGE_PAGES), and the same set for a plugin's own repository in the template's README.md and SECURITY.md.
- docs/plugins/<distribution>/index.md and its theme swatches: the page of every first-party plugin, written by
  `hz system plugin docs build` from the kernel (ADR 0082), and docs/plugins/README.md, the gallery: those pages, then
  the plugins the core knows of but doesn't ship (the reference company, docs/plugins/registry.toml).
- docs/schemas/contract-frontmatter-1.json: the JSON Schema of workflow contract frontmatter (ADR 0021).
- docs/schemas/team-graph-1.json and telemetry-entry-1.json: `hz graph --json` (JSON Graph Format v2, profile
  `hz-team`) and one line of a Heinzelmännchen's telemetry log (ADR 0054).

Usage: uv run python scripts/generate_docs.py [--check]
"""

# Project-bound: runs in the project environment (`uv run python`), not as an inline-metadata script, because it
# imports heinzel and its modules to read their operations, settings and schemas.

import json
import re
import subprocess
import sys
import tomllib
from html import escape
from pathlib import Path
from urllib.parse import quote, urlsplit

from pydantic import BaseModel
from pydantic_core import PydanticUndefined, to_jsonable_python

from heinzel import Config, HealthLet, Kernel, Settings, SettingsLet, SetupLet, TelemetryEntry
from heinzel.heads._exposure import exposed
from heinzel.heads.cli import build
from heinzel.heads.cli._chat import command_words
from heinzel.kernel import discover, env_prefix
from heinzel.modules.graph import TeamGraphDocument
from heinzel.modules.heinzel._models import SCHEMA_VERSION, Frontmatter
from heinzel.modules.system.config._schema import reference_hints, setting_schemas
from heinzel.modules.system.plugin.docs._build import rendered
from heinzel.modules.system.plugin.docs._files import write_pages
from heinzel.modules.system.plugin.docs._models import PluginPage
from heinzel.modules.system.plugin.docs._render import gallery
from heinzel.modules.system.plugin.docs._service import DocsService

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
README = ROOT / "README.md"
README_START, README_END = "<!-- modules:start -->", "<!-- modules:end -->"
BADGES_START, BADGES_END = "<!-- badges:start -->", "<!-- badges:end -->"
#: The top-level pages GitHub renders that carry the badges. Not LICENSE (raw text), not AGENTS.md and CLAUDE.md
#: (instructions every agent session loads), not CHANGELOG.md (the release process writes it).
BADGE_PAGES = ("README.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "INTENT.md")
OWNER, REPOSITORY = "helmut-hoffer-von-ankershoffen", "heinzel"
BEST_PRACTICES = 15863  # the OpenSSF Best Practices project id of this repository
TEMPLATE = ROOT / "template"
PLUGINS = DOCS / "plugins"
REGISTRY = PLUGINS / "registry.toml"
SCENES = DOCS / "scenes.toml"

MODULES_HEADER = """\
# Modules

Generated from the code by `make docs`; CI fails when it is out of date. Every operation is the same in every head:
a `hz` command, an MCP tool (`hz system serve mcp`), a REST endpoint (`hz system serve api`) and an endpoint of the web
app's API (`hz ui`, under `/api`, behind its session token). Plugins add their own modules; `hz system modules` lists
what is installed. Operations marked *expert* are listed by `hz --help`, shell completion and the web app only in expert
mode (`hz system expert on`); every one runs either way, and MCP and REST list them all (ADR 0041).
"""

CONFIGURATION_HEADER = """\
# Configuration reference

Generated from the code by `make docs`; CI fails when it is out of date. Settings come from TOML files — site, user
(`hz system config init` writes one), the nearest `hz.toml`, `$HZ_CONFIG` — and environment variables, which win.
`hz system config show` prints the values in effect; `hz system config set KEY VALUE` changes them.
Plugins add their own tables.
"""


def kernel() -> Kernel:
    """The built-in modules alone, with default settings: the same output on every machine."""
    config = Config.empty()
    records = discover(plugins=False)[0]
    return Kernel(config.settings(Settings, "hz"), records, config=config)


#: The app `typer ... utils docs` documents; built at import, as Typer expects a module-level app.
app = build(kernel(), plain=True)


def cli_reference() -> str:
    """Typer's Markdown reference of the CLI."""
    result = subprocess.run(
        [sys.executable, "-m", "typer", "scripts.generate_docs", "utils", "docs", "--name", "hz",
         "--title", "CLI reference"],
        capture_output=True, text=True, check=True, timeout=60, cwd=DOCS.parent,
        env={"PYTHONPATH": str(DOCS.parent), "COLUMNS": "120", "TERM": "dumb", "PATH": ""},
    )  # fmt: skip
    return result.stdout.rstrip() + "\n"


def configuration_reference() -> str:
    """Every setting as a table per section."""
    lines = [CONFIGURATION_HEADER]
    for section, settings in kernel().settings_of():
        schemas = setting_schemas(type(settings))
        user_only = type(settings).ignored_in_project
        lines += [
            f"## `[{section}]`",
            "",
            "| Key | Environment variable | Default | Description |",
            "| --- | --- | --- | --- |",
        ]
        for key, field in type(settings).model_fields.items():
            default = "platform-specific" if field.default is PydanticUndefined else f"`{_default(field.default)}`"
            text = (field.description or "") + reference_hints(schemas.get(key))
            if key in user_only and "project" not in text:
                text += " Never read from a project's hz.toml."
            lines.append(f"| `{key}` | `{env_prefix(section)}{key.upper()}` | {default} | {text.replace('|', '\\|')} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _default(value: object) -> str:
    """A default as written in Python, with models as the tables a configuration file holds."""
    if isinstance(value, BaseModel) or (isinstance(value, list) and any(isinstance(v, BaseModel) for v in value)):
        return repr(to_jsonable_python(value))
    return repr(value)


def _anchor(qualname: str) -> str:
    return qualname.replace(".", "")


def module_catalog() -> str:
    """A section per module: summary, operations in every head, settings and health checks."""
    k = kernel()
    surfaces = {head: exposed(k, head) for head in ("cli", "mcp", "api", "web")}
    lines = [MODULES_HEADER, "| Module | What it does |", "| --- | --- |"]
    lines += [f"| [`{r.qualname}`](#{_anchor(r.qualname)}) | {r.module.summary} |" for r in k.modules]
    lines.append("")
    for record in k.modules:
        lines += [f"## `{record.qualname}`", "", record.module.summary, ""]
        if not record.origin.builtin and record.parent is None:
            lines += [
                (
                    f"First-party plugin `{record.origin.distribution}`: "
                    f"`hz system plugin add {record.qualname} --confirm`."
                ),
                "",
            ]
        operations = [item for item in surfaces["cli"] if item.record.qualname == record.qualname]
        if operations:
            lines += [
                "| Operation | CLI | MCP tool | REST | Web | Changes state | Expert |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
            for item in operations:
                in_mcp = any(o.operation is item.operation for o in surfaces["mcp"])
                in_api = any(o.operation is item.operation for o in surfaces["api"])
                in_web = any(o.operation is item.operation for o in surfaces["web"])
                verb = "GET" if item.operation.read_only else "POST"
                path = "/".join(item.path)
                lines.append(
                    f"| {item.operation.summary} | `hz {' '.join(command_words(item.path))}` | "
                    f"{f'`{item.name}`' if in_mcp else '—'} | "
                    f"{f'`{verb} /{path}`' if in_api else '—'} | "
                    f"{f'`{verb} /api/{path}`' if in_web else '—'} | "
                    f"{'' if item.operation.read_only else 'yes'} | "
                    f"{'expert' if item.operation.audience == 'expert' else ''} |"
                )
            lines.append("")
        settings = [let for owner, let in k.lets(SettingsLet) if owner.qualname == record.qualname]
        if settings:
            lines += [f"Settings: [`[{record.qualname}]`](CONFIGURATION.md#{_anchor(record.qualname)}).", ""]
        if any(owner.qualname == record.qualname for owner, _ in k.lets(HealthLet)):
            lines += ["Health checks: reported by `hz system doctor`.", ""]
        step = next((let for owner, let in k.lets(SetupLet) if owner.qualname == record.qualname), None)
        if step is not None:
            kind = "required" if step.required else "optional"
            lines += [f"Setup: `hz setup {record.qualname}` ({kind}), also part of `hz setup`.", ""]
    return "\n".join(lines).rstrip() + "\n"


def plugin_pages() -> dict[Path, str]:
    """The page of every first-party plugin and the gallery, as `hz system plugin docs build` writes them."""
    k = kernel()
    documentation = k.resolve(DocsService).documentation(assets=PLUGINS)
    files = {PLUGINS / relative: text for relative, text in rendered(documentation, index=False).items()}
    files[PLUGINS / "README.md"] = plugin_gallery(documentation.pages)
    return files


def plugin_gallery(pages: list[PluginPage]) -> str:
    """The gallery: first-party pages and registered community plugins.

    Raises:
        ValueError: If a registry distribution or HTTPS link is unsafe.
    """
    registry = tomllib.loads(REGISTRY.read_text(encoding="utf-8")) if REGISTRY.is_file() else {}
    lines = [
        gallery(pages, title="Plugin gallery").rstrip(),
        "",
        (
            "Every page is generated from the installed plugin by `hz system plugin docs build` (ADR 0082); CI fails "
            "when one is out of date. A plugin's own repository documents it the same way: `make docs-plugins` there "
            "writes the same page, `make docs-record` its recordings."
        ),
        "",
        "## More plugins",
        "",
        (
            "Install a discovered plugin with `hz system plugin add <spec> --confirm`. "
            "`hz system plugin search` discovers plugin metadata in configured repository directories and external "
            "repositories with the topic `heinzel-plugin`; no registry pull request is needed. "
            "The optional [registry.toml](registry.toml) curates additional documentation links below."
        ),
        "",
        "| Plugin | What it does | Repository | Documentation |",
        "| --- | --- | --- | --- |",
    ]
    for entry in sorted(registry.get("plugin", []), key=lambda item: str(item.get("name", ""))):
        name = entry.get("name", "")
        if not isinstance(name, str) or re.fullmatch(r"heinzel-[a-z0-9]+(?:-[a-z0-9]+)*", name) is None:
            msg = "Plugin registry names must be lowercase heinzel-<name> distributions"
            raise ValueError(msg)
        for field in ("repository", "docs"):
            value = entry.get(field)
            if value is None and field == "docs":
                continue
            if not _registry_url(value):
                msg = f"Plugin registry {field} must be an HTTPS URL without credentials or Markdown delimiters"
                raise ValueError(msg)
        docs = entry.get("docs")
        link = f"[page]({docs})" if docs else f"[README]({entry['repository']}#readme)"
        summary = _registry_summary(entry.get("summary", ""))
        lines.append(f"| `{name}` | {summary} | <{entry['repository']}> | {link} |")
    return "\n".join(lines) + "\n"


def _registry_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        _ = url.port  # Reject malformed or out-of-range ports before publishing a broken link.
    except ValueError:
        return False
    return bool(
        url.scheme == "https"
        and url.hostname
        and not url.username
        and not url.password
        and all(
            character.isprintable() and not character.isspace() and character not in '<>()\\|`[]"'
            for character in value
        )
    )


def _registry_summary(value: object) -> str:
    text = escape("".join(character if character.isprintable() else " " for character in str(value)), quote=False)
    for character in ("\\", "`", "[", "]", "*", "_", "!"):
        text = text.replace(character, "\\" + character)
    return text.replace("|", "&#124;")


def with_block(text: str, start_marker: str, end_marker: str, body: str) -> str:
    """The text with `body` between its two markers."""
    start, end = text.index(start_marker) + len(start_marker), text.index(end_marker)
    return text[:start] + "\n" + body + "\n" + text[end:]


def readme_with_module_list(readme: str) -> str:
    """README with the generated module list between its markers."""
    k = kernel()
    items = [
        f"- [`{r.qualname}`](docs/MODULES.md#{_anchor(r.qualname)}) — {r.module.summary}"
        + ("" if r.origin.builtin else f" *First-party plugin: `hz system plugin add {r.path[0]} --confirm`.*")
        for r in k.modules
    ]
    return with_block(readme, README_START, README_END, "\n".join(items))


def python_versions() -> list[str]:
    """The Python versions pyproject.toml's classifiers declare, which CI tests: the source of the Python badge."""
    classifiers = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["classifiers"]
    prefix = "Programming Language :: Python :: 3."
    return [c.removeprefix("Programming Language :: Python :: ") for c in classifiers if c.startswith(prefix)]


def badges(
    owner: str,
    repository: str,
    best_practices: int | None = None,
    pypi_project: str | None = None,
    *,
    sonar: bool = False,
) -> str:
    """The badges of a repository, one per line: what it is checked with, how healthy it is, what it runs on.

    `best_practices` is this repository's OpenSSF registration; a new plugin has none. `pypi_project` selects the
    registry version badge only for a registered project; unpublished plugins retain their GitHub release badge.
    Sonar badges require an imported project; scaffolding a plugin does not provision one.
    """
    github = f"https://github.com/{owner}/{repository}"
    scorecard = f"github.com/{owner}/{repository}"
    shield = "https://img.shields.io"
    versions = quote(" | ".join(python_versions()))
    coverage_data = (
        f"https://raw.githubusercontent.com/{owner}/{repository}/python-coverage-comment-action-data/endpoint.json"
    )
    items = [
        (
            "CI",
            f"{shield}/github/actions/workflow/status/{owner}/{repository}/ci.yml?branch=main&label=CI",
            f"{github}/actions/workflows/ci.yml",
        ),
        ("Coverage", f"{shield}/endpoint?url={coverage_data}", f"{github}/tree/python-coverage-comment-action-data"),
        (
            "CodeQL",
            f"{shield}/github/actions/workflow/status/{owner}/{repository}/codeql.yml?branch=main&label=CodeQL",
            f"{github}/actions/workflows/codeql.yml",
        ),
        (
            "OpenSSF Scorecard",
            f"{shield}/ossf-scorecard/{scorecard}?label=OpenSSF%20Scorecard",
            f"https://scorecard.dev/viewer/?uri={scorecard}",
        ),
        *(
            [
                (
                    "OpenSSF Best Practices",
                    f"{shield}/cii/level/{best_practices}?label=OpenSSF%20Best%20Practices",
                    f"https://www.bestpractices.dev/en/projects/{best_practices}/passing",
                )
            ]
            if best_practices
            else []
        ),
        (
            ("PyPI", f"{shield}/pypi/v/{pypi_project}", f"https://pypi.org/project/{pypi_project}/")
            if pypi_project
            else ("Release", f"{shield}/github/v/release/{owner}/{repository}?sort=semver", f"{github}/releases")
        ),
        ("Python", f"{shield}/badge/python-{versions}-blue", "pyproject.toml"),
        *(
            [
                (
                    "Quality Gate Status",
                    f"{shield}/sonar/quality_gate/{owner}_{repository}?server=https://sonarcloud.io&label=Quality%20Gate%20Status",
                    f"https://sonarcloud.io/summary/overall?id={owner}_{repository}",
                ),
                (
                    # Shields shows this rating as a number; Sonar's own badge shows the letter, and its red for E.
                    "Security Rating",
                    f"https://sonarcloud.io/api/project_badges/measure?project={owner}_{repository}&metric=security_rating",
                    f"https://sonarcloud.io/summary/overall?id={owner}_{repository}",
                ),
            ]
            if sonar
            else []
        ),
        ("License: MIT", f"{shield}/badge/license-MIT-brightgreen", "LICENSE"),
    ]
    return "\n".join(f"[![{alt}]({image})]({link})" for alt, image, link in items)


def template_badges() -> str:
    """The badges of a plugin's own repository, in the template's placeholders."""
    return badges("[[ github_owner ]]", "[[ project_name ]]")


def template_security() -> str:
    """The template's SECURITY.md: ours, with the plugin's badges ([# starts a comment in the template's Jinja)."""
    ours = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    return with_block(ours, BADGES_START, BADGES_END, template_badges()).replace("[#", '[[ "[#" ]]')


def contract_schema() -> str:
    """The JSON Schema of contract frontmatter, generated from the pydantic models that validate it."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:heinzel:contract-frontmatter:{SCHEMA_VERSION}",
        **Frontmatter.model_json_schema(by_alias=True),
    }
    schema["patternProperties"] = {"^x-": {"description": "Extension keys, ignored."}}
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def model_schema(model: type[BaseModel], urn: str, title: str) -> str:
    """The JSON Schema of a model that crosses a boundary, generated so it can't drift from the code."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": urn,
        **model.model_json_schema(mode="serialization"),
        "title": title,
    }
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    """Write the references, or with --check only compare them."""
    check = "--check" in sys.argv
    plugin_outputs = {path.relative_to(PLUGINS).as_posix(): text for path, text in plugin_pages().items()}
    plugin_stale, _ = write_pages(plugin_outputs, PLUGINS, check=check)
    outputs = {
        DOCS / "MODULES.md": module_catalog(),
        DOCS / "CLI_REFERENCE.md": cli_reference(),
        DOCS / "CONFIGURATION.md": configuration_reference(),
        DOCS / "schemas" / f"contract-frontmatter-{SCHEMA_VERSION}.json": contract_schema(),
        DOCS / "schemas" / "team-graph-1.json": model_schema(
            TeamGraphDocument, "urn:heinzel:team-graph:1", "hz-team: a team graph in JSON Graph Format v2"
        ),
        DOCS / "schemas" / "telemetry-entry-1.json": model_schema(
            TelemetryEntry, "urn:heinzel:telemetry-entry:1", "One entry of a Heinzelmännchen's telemetry log"
        ),
    }
    ours = badges(OWNER, REPOSITORY, BEST_PRACTICES, pypi_project="heinzel", sonar=True)
    for page in BADGE_PAGES:
        text = with_block((ROOT / page).read_text(encoding="utf-8"), BADGES_START, BADGES_END, ours)
        outputs[ROOT / page] = readme_with_module_list(text) if page == README.name else text
    command = (DOCS / "_data" / "install-command.txt").read_text(encoding="utf-8").strip()
    for path in (README, ROOT / "PYPI.md"):
        body = f"<!-- docs-test -->\n```sh\n{command}\n```"
        outputs[path] = with_block(
            outputs.get(path, path.read_text(encoding="utf-8")), "<!-- install:start -->", "<!-- install:end -->", body
        )
        guided = (DOCS / "_data" / "install-script-command.txt").read_text(encoding="utf-8").strip()
        outputs[path] = with_block(
            outputs[path], "<!-- install-script:start -->", "<!-- install-script:end -->", f"```sh\n{guided}\n```"
        )
    readme = TEMPLATE / "README.md.jinja"
    outputs[readme] = with_block(readme.read_text(encoding="utf-8"), BADGES_START, BADGES_END, template_badges())
    outputs[TEMPLATE / "SECURITY.md.jinja"] = template_security()
    stale = [path for path, content in outputs.items() if not path.exists() or path.read_text("utf-8") != content]
    if check:
        if stale or plugin_stale:
            print("Out of date; run `make docs`:", *plugin_stale, *(p.name for p in stale), sep="\n  ", file=sys.stderr)
            return 1
        return 0
    for path in stale:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(outputs[path], encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
