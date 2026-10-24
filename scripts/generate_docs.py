"""Generate every document that can be derived from the code. `--check` fails if any of them is out of date.

- docs/MODULES.md: every module with its operations in every head (CLI, MCP, REST), settings and health checks.
- docs/CLI_REFERENCE.md: Typer's own `utils docs` output for the CLI built from the built-in modules.
- docs/CONFIGURATION.md: every setting of the kernel and the built-in modules, with environment variable and default.
- README.md: the module list between `<!-- modules:start -->` and `<!-- modules:end -->`.
- docs/schemas/contract-frontmatter-1.json: the JSON Schema of workflow contract frontmatter (ADR 0021).

Usage: uv run python scripts/generate_docs.py [--check]
"""

# Project-bound: runs in the project environment (`uv run python`), not as an inline-metadata script, because it
# imports heinzel and its modules to read their operations, settings and schemas.

import importlib
import json
import subprocess
import sys
from pathlib import Path

from pydantic_core import PydanticUndefined

from heinzel import Config, HealthLet, Kernel, Settings, SettingsLet, __version__
from heinzel.heads._exposure import exposed
from heinzel.heads.cli import build
from heinzel.kernel import Origin, discover, env_prefix, walk
from heinzel.modules.system.plugin._service import FIRST_PARTY
from heinzel.modules.workflow._models import SCHEMA_VERSION, Frontmatter

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
README = ROOT / "README.md"
README_START, README_END = "<!-- modules:start -->", "<!-- modules:end -->"

MODULES_HEADER = """\
# Modules

Generated from the code by `make docs`; CI fails when it is out of date. Every operation is the same in every head:
a `hz` command, an MCP tool (`hz system serve mcp`) and a REST endpoint (`hz system serve api`). Plugins add their own
modules; `hz system modules` lists what is installed.
"""

CONFIGURATION_HEADER = """\
# Configuration reference

Generated from the code by `make docs`; CI fails when it is out of date. Settings come from TOML files — site, user
(`hz system config init` writes one), the nearest `hz.toml`, `$HZ_CONFIG` — and environment variables, which win.
`hz system config show` prints the values in effect; `hz system config set KEY VALUE` changes them.
Plugins add their own tables.
"""


def kernel() -> Kernel:
    """The built-in modules plus the first-party plugins, with default settings: the same output on every machine."""
    config = Config.empty()
    records = discover(plugins=False)[0]
    for name in sorted(FIRST_PARTY):
        package = importlib.import_module(f"heinzel_{name}")
        records += walk(package, Origin(distribution=f"heinzel-{name}", version=__version__, builtin=False))
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
        lines += [
            f"## `[{section}]`",
            "",
            "| Key | Environment variable | Default | Description |",
            "| --- | --- | --- | --- |",
        ]
        for key, field in type(settings).model_fields.items():
            default = "platform-specific" if field.default is PydanticUndefined else f"`{field.default!r}`"
            lines.append(f"| `{key}` | `{env_prefix(section)}{key.upper()}` | {default} | {field.description or ''} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _anchor(qualname: str) -> str:
    return qualname.replace(".", "")


def module_catalog() -> str:
    """A section per module: summary, operations in every head, settings and health checks."""
    k = kernel()
    surfaces = {"cli": exposed(k, "cli"), "mcp": exposed(k, "mcp"), "api": exposed(k, "api")}
    lines = [MODULES_HEADER, "| Module | What it does |", "| --- | --- |"]
    lines += [f"| [`{r.qualname}`](#{_anchor(r.qualname)}) | {r.module.summary} |" for r in k.modules]
    lines.append("")
    for record in k.modules:
        lines += [f"## `{record.qualname}`", "", record.module.summary, ""]
        if not record.origin.builtin and record.parent is None:
            lines += [
                f"First-party plugin `{record.origin.distribution}`: `hz system plugin add {record.qualname}`.",
                "",
            ]
        operations = [item for item in surfaces["cli"] if item.record.qualname == record.qualname]
        if operations:
            lines += ["| Operation | CLI | MCP tool | REST | Changes state |", "| --- | --- | --- | --- | --- |"]
            for item in operations:
                in_mcp = any(o.operation is item.operation for o in surfaces["mcp"])
                in_api = any(o.operation is item.operation for o in surfaces["api"])
                verb = "GET" if item.operation.read_only else "POST"
                lines.append(
                    f"| {item.operation.summary} | `hz {' '.join(item.path)}` | "
                    f"{f'`{item.name}`' if in_mcp else '—'} | "
                    f"{f'`{verb} /' + '/'.join(item.path) + '`' if in_api else '—'} | "
                    f"{'' if item.operation.read_only else 'yes'} |"
                )
            lines.append("")
        settings = [let for owner, let in k.lets(SettingsLet) if owner.qualname == record.qualname]
        if settings:
            lines += [f"Settings: [`[{record.qualname}]`](CONFIGURATION.md#{_anchor(record.qualname)}).", ""]
        if any(owner.qualname == record.qualname for owner, _ in k.lets(HealthLet)):
            lines += ["Health checks: reported by `hz system doctor`.", ""]
    return "\n".join(lines).rstrip() + "\n"


def readme_with_module_list(readme: str) -> str:
    """README with the generated module list between its markers."""
    k = kernel()
    items = [
        f"- [`{r.qualname}`](docs/MODULES.md#{_anchor(r.qualname)}) — {r.module.summary}"
        + ("" if r.origin.builtin else f" *First-party plugin: `hz system plugin add {r.path[0]}`.*")
        for r in k.modules
    ]
    start, end = readme.index(README_START) + len(README_START), readme.index(README_END)
    return readme[:start] + "\n" + "\n".join(items) + "\n" + readme[end:]


def contract_schema() -> str:
    """The JSON Schema of contract frontmatter, generated from the pydantic models that validate it."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:heinzel:contract-frontmatter:{SCHEMA_VERSION}",
        **Frontmatter.model_json_schema(by_alias=True),
    }
    schema["patternProperties"] = {"^x-": {"description": "Extension keys, ignored."}}
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    """Write the references, or with --check only compare them."""
    outputs = {
        DOCS / "MODULES.md": module_catalog(),
        DOCS / "CLI_REFERENCE.md": cli_reference(),
        DOCS / "CONFIGURATION.md": configuration_reference(),
        README: readme_with_module_list(README.read_text(encoding="utf-8")),
        DOCS / "schemas" / f"contract-frontmatter-{SCHEMA_VERSION}.json": contract_schema(),
    }
    stale = [path for path, content in outputs.items() if not path.exists() or path.read_text("utf-8") != content]
    if "--check" in sys.argv:
        if stale:
            print("Out of date; run `make docs`:", *(p.name for p in stale), sep="\n  ", file=sys.stderr)
            return 1
        return 0
    for path in stale:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(outputs[path], encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
