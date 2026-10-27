"""Machine-readable discovery from the executable CLI and shared operation schemas."""

import inspect
from typing import Any

import typer
from pydantic import TypeAdapter
from pydantic_core import to_jsonable_python
from typer.core import TyperGroup, TyperOption

from heinzel.heads._exposure import exposed, input_schema
from heinzel.heads.cli._chat import command_words
from heinzel.kernel import Kernel


def command_tree(kernel: Kernel, ctx: typer.Context) -> dict[str, Any]:
    """Describe every installed command, including hidden commands, without invoking any of them.

    Click supplies the actual argument and option syntax, including head-only commands and deprecated aliases.
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
            "parameters": [
                {
                    "name": parameter.name,
                    "kind": "option" if isinstance(parameter, TyperOption) else "argument",
                    "opts": parameter.opts,
                    "secondary_opts": parameter.secondary_opts,
                    "required": parameter.required,
                    "default": to_jsonable_python(parameter.default, fallback=str),
                    "type": parameter.type.name,
                    "nargs": parameter.nargs,
                    "multiple": parameter.multiple,
                    "envvar": parameter.envvar,
                    "help": getattr(parameter, "help", None),
                    "metavar": parameter.metavar,
                    "is_flag": getattr(parameter, "is_flag", False),
                    "choices": getattr(parameter.type, "choices", None),
                }
                for parameter in command.get_params(context)
            ],
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
