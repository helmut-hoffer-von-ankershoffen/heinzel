"""The reference of every operation and tool: one renderer for `hz system tools` and the generated documents."""

import json
from collections.abc import Iterable
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, TypeAdapter

from heinzel.kernel import Exposed, HeinzelError, Kernel, ServedTool, ServedTools, command_words, exposed, input_schema

Format = Literal["text", "markdown", "yaml", "json"]
HeadFilter = Literal["all", "cli", "mcp", "rest", "web"]
#: Our head names, by the name a person gives the head.
HEADS = {"cli": "cli", "mcp": "mcp", "rest": "api", "web": "web"}


class ParameterInfo(BaseModel):
    """One parameter a caller supplies."""

    name: str = Field(description="Parameter name.")
    type: str = Field(description="Its type, e.g. `string`, `integer`, `list[string]`, `'a' | 'b'`.")
    required: bool = Field(description="Whether the caller must supply it.")
    default: Any = Field(description="Its default; none when it is required or defaults to nothing.")
    description: str = Field(description="What it is for.")


class HeadInfo(BaseModel):
    """How each head exposes an operation; none where the head does not."""

    cli: str | None = Field(description="The command, e.g. `hz system doctor`.")
    mcp: str | None = Field(description="The MCP tool name, e.g. `system_doctor`.")
    rest: str | None = Field(description="The REST endpoint, e.g. `GET /system/doctor`.")
    web: str | None = Field(description="The endpoint of the web app's API, e.g. `GET /api/system/doctor`.")


class ToolInfo(BaseModel):
    """An operation, with everything a head shows of it."""

    module: str = Field(description="Qualified name of the module that owns it.")
    name: str = Field(description="The operation's name.")
    summary: str = Field(description="What it does, in one sentence.")
    heads: HeadInfo = Field(description="Its CLI command, MCP tool name, REST endpoint and web endpoint.")
    read_only: bool = Field(description="Whether it leaves everything as it is.")
    private: bool = Field(description="Whether it needs a trusted call identity and is left out of network heads.")
    audience: str = Field(description="`everyday`, or `expert`: listed by help and the web app only in expert mode.")
    parameters: list[ParameterInfo] = Field(description="What a caller supplies.")
    result: str = Field(description="The shape of its result.")
    input_schema: dict[str, Any] = Field(description="The JSON Schema of its input, as the MCP tool declares it.")


class ToolReference(BaseModel):
    """The reference, rendered."""

    format: Format = Field(description="The format of `text`.")
    count: int = Field(description="How many operations it describes.")
    text: str = Field(description="The reference in that format.")


def _resolve(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    return defs.get(ref.rsplit("/", 1)[-1], schema) if isinstance(ref, str) else schema


def type_name(schema: dict[str, Any], defs: dict[str, Any] | None = None) -> str:
    """A JSON Schema's type for people: `string`, `list[integer]`, `'a' | 'b'`, a model's name."""
    defs = defs or {}
    if isinstance(ref := schema.get("$ref"), str):
        return ref.rsplit("/", 1)[-1]
    if "enum" in schema or "const" in schema:
        return " | ".join(repr(v) for v in schema.get("enum", [schema.get("const")]))
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return " | ".join(type_name(s, defs) for s in schema[key])
    kind = schema.get("type", "any")
    if kind == "array":
        return f"list[{type_name(schema.get('items', {}), defs)}]"
    if kind == "object" and "additionalProperties" in schema and isinstance(schema["additionalProperties"], dict):
        return f"dict[string, {type_name(schema['additionalProperties'], defs)}]"
    return ", ".join(kind) if isinstance(kind, list) else str(kind)


def _result(item: Exposed) -> str:
    """The shape of a result: its type and, for a model, the fields."""
    try:
        schema = TypeAdapter(item.returns).json_schema(mode="serialization")
    except Exception:  # noqa: BLE001 - an annotation pydantic cannot describe is a result without a shape
        return "any"
    defs = schema.get("$defs", {})
    inner = schema.get("items", {}) if schema.get("type") == "array" else schema
    model = _resolve(inner, defs)
    name = str(model["title"]) if model is schema and "title" in model else type_name(schema, defs)
    if model.get("type") != "object" or "properties" not in model:
        return name
    fields = ", ".join(f"{k}: {type_name(v, defs)}" for k, v in model["properties"].items())
    return f"{name} {{{fields}}}"


def _parameters(schema: dict[str, Any]) -> list[ParameterInfo]:
    defs, required = schema.get("$defs", {}), set(schema.get("required", []))
    return [
        ParameterInfo(
            name=name,
            type=type_name(prop, defs),
            required=name in required,
            default=prop.get("default"),
            description=str(prop.get("description", "")),
        )
        for name, prop in schema.get("properties", {}).items()
    ]


def served_mcp_tools(kernel: Kernel) -> dict[str, ServedTool]:
    """The tools the stdio MCP server lists, by name: what a client sees, with its schemas, hints and aliases.

    Raises:
        HeinzelError: If no head bound the service that lists them.
    """
    return kernel.resolve(ServedTools).tools()


def tools(kernel: Kernel, *, modules: Iterable[str] = (), head: HeadFilter = "all") -> list[ToolInfo]:
    """Every operation the kernel's modules expose in any head, in module order, optionally of some modules or one head.

    A module filter matches the module and the modules under it (`system` includes `system.config`).

    Raises:
        HeinzelError: If a module filter names no module.
    """
    wanted = tuple(modules)
    known = {r.qualname for r in kernel.modules}
    if unknown := [w for w in wanted if not any(q == w or q.startswith(f"{w}.") for q in known)]:
        msg = f"No module {', '.join(unknown)}; `hz system modules` lists them"
        raise HeinzelError(msg)
    served = served_mcp_tools(kernel)
    surfaces = {
        key: {(i.record.qualname, i.operation.name): i for i in exposed(kernel, name)} for key, name in HEADS.items()
    }
    order: dict[tuple[str, str], Exposed] = {}
    for surface in surfaces.values():
        for key, item in surface.items():
            order.setdefault(key, item)
    position = {record.qualname: n for n, record in enumerate(kernel.modules)}
    result: list[ToolInfo] = []
    for key, item in sorted(order.items(), key=lambda kv: position.get(kv[0][0], 0)):
        qualname = item.record.qualname
        if wanted and not any(qualname == w or qualname.startswith(f"{w}.") for w in wanted):
            continue
        # What each head really serves: MCP only what the server lists (it leaves some operations to a chat's own
        # pipe), so the column and the filter read the same source.
        runs = {name: key in surface for name, surface in surfaces.items()}
        runs["mcp"] = runs["mcp"] and item.name in served
        if not any(runs.values()) or (head != "all" and not runs[head]):
            continue
        verb = "GET" if item.operation.read_only else "POST"
        path = "/".join(item.path)
        schema = dict(served[item.name].input_schema) if runs["mcp"] else input_schema(item)
        result.append(
            ToolInfo(
                module=qualname,
                name=item.operation.name,
                summary=item.operation.summary,
                heads=HeadInfo(
                    cli=f"hz {' '.join(command_words(item.path))}" if runs["cli"] else None,
                    mcp=item.name if runs["mcp"] else None,
                    rest=f"{verb} /{path}" if runs["rest"] else None,
                    web=f"{verb} /api/{path}" if runs["web"] else None,
                ),
                read_only=item.operation.read_only,
                private=item.operation.private,
                audience=item.operation.audience,
                parameters=_parameters(schema),
                result=_result(item),
                input_schema=schema,
            )
        )
    return result


def _flag(entry: ToolInfo) -> str:
    return ", ".join(
        [
            "read-only" if entry.read_only else "changes state",
            *(["private"] if entry.private else []),
            entry.audience,
        ]
    )


def _heads(entry: ToolInfo, head: HeadFilter) -> list[tuple[str, str]]:
    shown = [("CLI", entry.heads.cli), ("MCP", entry.heads.mcp), ("REST", entry.heads.rest), ("Web", entry.heads.web)]
    return [(k, v) for k, v in shown if v and (head == "all" or k.lower() == head)]


def _text(entries: list[ToolInfo], head: HeadFilter) -> str:
    blocks: list[str] = []
    for e in entries:
        lines = [f"{e.module}  {e.name}: {e.summary}", f"  {_flag(e)}"]
        lines += [f"  {k + ':':5} {v}" for k, v in _heads(e, head)]
        for p in e.parameters:
            default = "required" if p.required else f"default {json.dumps(p.default)}"
            lines.append(f"  param {p.name} ({p.type}, {default}): {p.description}")
        lines.append(f"  result: {e.result}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _markdown(entries: list[ToolInfo], head: HeadFilter) -> str:
    lines: list[str] = []
    module = None
    for e in entries:
        if e.module != module:
            module = e.module
            lines += [f"## `{module}`", ""]
        lines += [f"### `{e.module}` {e.name}", "", e.summary, "", f"{_flag(e).capitalize()}.", ""]
        lines += [f"- {k}: `{v}`" for k, v in _heads(e, head)]
        if e.parameters:
            lines += ["", "| Parameter | Type | Default | Description |", "| --- | --- | --- | --- |"]
            lines += [
                f"| `{p.name}` | `{_cell(p.type)}` | "
                f"{'required' if p.required else '`' + _cell(json.dumps(p.default)) + '`'} | {_cell(p.description)} |"
                for p in e.parameters
            ]
        lines += ["", f"Result: `{_cell(e.result)}`", ""]
    return "\n".join(lines).rstrip() + "\n"


def _data(entries: list[ToolInfo], head: HeadFilter) -> list[dict[str, Any]]:
    data = [e.model_dump(mode="json", exclude={"input_schema"} if head == "all" else set()) for e in entries]
    if head != "all":
        for d in data:
            d["heads"] = {k: v for k, v in d["heads"].items() if k == head}
    return data


def render(entries: list[ToolInfo], fmt: Format = "text", head: HeadFilter = "all") -> str:
    """The reference in a format; `head` narrows the head columns to one.

    `input_schema` is part of the data only when one head is asked for, where it is that head's input.
    """
    if fmt == "markdown":
        return _markdown(entries, head)
    if fmt == "yaml":
        return yaml.safe_dump(_data(entries, head), sort_keys=False, allow_unicode=True, width=120)
    if fmt == "json":
        return json.dumps(_data(entries, head), indent=2, ensure_ascii=False) + "\n"
    return _text(entries, head)


MCP_HEADER = """\
# MCP reference

Generated from the code by `make docs`; CI fails when it is out of date. `hz system serve mcp` is an MCP server with one
tool per operation below (`hz system tools --head mcp` lists the tools of what you have installed, plugins included).
Over stdio every tool below is served (tools whose description starts with *Deprecated* are old names that still run the
new tool); streamable HTTP leaves out the tools marked *private* and the ones restricted to stdio.
Each tool lists its description and the JSON Schema of its input.
"""


def mcp_reference(entries: list[ToolInfo], served: dict[str, ServedTool]) -> str:
    """Every tool the stdio MCP server lists, as a client sees it: description, hints and JSON input schema."""
    by_name = {e.heads.mcp: e for e in entries if e.heads.mcp}
    lines = [MCP_HEADER]
    for name, tool in served.items():
        read_only = tool.read_only
        entry = by_name.get(name)
        origin = f"Module `{entry.module}`. " if entry else ""
        result = f" Result: `{_cell(entry.result)}`." if entry else ""
        lines += [
            f"## `{name}`",
            "",
            tool.description,
            "",
            f"{origin}{'Read-only' if read_only else 'Changes state'}.{result}",
            "",
            "```json",
            json.dumps(tool.input_schema, indent=2, ensure_ascii=False),
            "```",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"
