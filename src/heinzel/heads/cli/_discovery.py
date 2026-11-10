"""Machine-readable discovery from the executable CLI and shared operation schemas."""

from __future__ import annotations

import inspect
from enum import Enum
from typing import TYPE_CHECKING, Any, get_args

import typer
from pydantic import TypeAdapter
from pydantic_core import to_jsonable_python
from typer.core import TyperGroup, TyperOption

from heinzel.heads._exposure import exposed, input_schema
from heinzel.heads.cli._chat import command_words

if TYPE_CHECKING:
    from collections.abc import Sequence

    from typer._click import Parameter
    from typer._coercion import TypeDescriptor

    from heinzel.kernel import Kernel


def _descriptor_metadata(descriptor: TypeDescriptor) -> tuple[str, Sequence[object] | None]:
    """Keep schema v1's Click type names and choice values with Typer's new descriptors."""
    if descriptor.is_list or descriptor.is_tuple:
        from typer._coercion import resolve_type_descriptor  # noqa: PLC0415 - only available with descriptors

        items = [
            _descriptor_metadata(resolve_type_descriptor(item, descriptor.parameter_info))
            for item in get_args(descriptor.annotation)
        ]
        if descriptor.is_list:
            return items[0]
        return f"<{' '.join(name for name, _ in items)}>", None

    if (parser := descriptor.parameter_info.parser) is not None:
        return getattr(parser, "__name__", "function"), None
    if (choices := descriptor.choices) is not None:
        return "choice", tuple(choice.value if isinstance(choice, Enum) else choice for choice in choices)
    if descriptor.is_path:
        name = descriptor.path_type
    elif descriptor.is_file:
        name = "filename"
    elif descriptor.is_ranged:
        name = f"{descriptor.ranged_type_name} range"
    elif descriptor.annotation is bool:
        name = "boolean"
    else:
        annotation = descriptor.annotation
        name = getattr(annotation, "__name__", str(annotation)).lower()
    return name, None


def _describe_parameter(parameter: Parameter) -> dict[str, Any]:
    descriptor: TypeDescriptor | None = getattr(parameter, "type_descriptor", None)
    if descriptor is not None:
        type_name, choices = _descriptor_metadata(descriptor)
    else:
        legacy_type = getattr(parameter, "type")  # noqa: B009 - only older Typer versions define this attribute
        type_name, choices = legacy_type.name, getattr(legacy_type, "choices", None)
    return {
        "name": parameter.name,
        "kind": "option" if isinstance(parameter, TyperOption) else "argument",
        "opts": parameter.opts,
        "secondary_opts": parameter.secondary_opts,
        "required": parameter.required,
        "default": to_jsonable_python(parameter.default, fallback=str),
        "type": type_name,
        "nargs": parameter.nargs,
        "multiple": parameter.multiple,
        "envvar": parameter.envvar,
        "help": getattr(parameter, "help", None),
        "metavar": parameter.metavar,
        "is_flag": getattr(parameter, "is_flag", False),
        "choices": choices,
    }


def command_tree(kernel: Kernel, ctx: typer.Context) -> dict[str, Any]:
    """Describe every installed command, including hidden commands, without invoking any of them.

    Typer supplies the actual argument and option syntax, including head-only commands and deprecated aliases.
    Operation schemas supply nested input constraints and output shapes without injecting services or settings.
    """
    operations = {command_words(item.path): item for item in exposed(kernel, "cli")}

    def describe(context: typer.Context, path: tuple[str, ...]) -> dict[str, Any]:
        command = context.command
        result: dict[str, Any] = {
            "command": " ".join(("hz", *path)),
            "help": inspect.cleandoc(command.help or ""),
            "hidden": command.hidden,
            "deprecated": command.deprecated or (command.help or "").startswith("Deprecated:"),
            "parameters": [_describe_parameter(parameter) for parameter in command.get_params(context)],
        }
        if item := operations.get(path):
            result["operation"] = {
                "module": item.record.qualname,
                "name": item.operation.name,
                "read_only": item.operation.read_only,
                "audience": item.operation.audience,
                "input_schema": input_schema(item),
                "output_schema": TypeAdapter(item.returns).json_schema(mode="serialization"),
            }
        if isinstance(command, TyperGroup):
            children: list[dict[str, Any]] = []
            for name in sorted(command.list_commands(context)):
                child = command.get_command(context, name)
                if child is not None:
                    child_context = typer.Context(child, info_name=name, parent=context)
                    with child_context:
                        children.append(describe(child_context, (*path, name)))
            result["commands"] = children
        return result

    return {"schema_version": 1, "root": describe(ctx, ())}
