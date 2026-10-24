"""The expert module of system: how much help, completion and the web app list (ADR 0041).

By default they list the everyday operations, the ones a newcomer needs; expert mode lists every one. Hidden is not
disabled: every command runs when typed, and MCP and REST always list every operation.
"""

from rich.console import RenderableType
from rich.text import Text

from heinzel import Module
from heinzel.modules.system.expert._models import ExpertMode, OperationAudience
from heinzel.modules.system.expert._service import ExpertService

module = Module("expert", "Expert mode: list every command, or just the everyday ones.", essential=True)
module.service(ExpertService)


def render(result: ExpertMode) -> RenderableType:
    """The mode, what it means, and how to switch."""
    if result.expert:
        lines = [
            "Expert mode is on: help, shell completion and the web app list every command.",
            "Back to the everyday commands: hz system expert off",
        ]
    else:
        lines = [
            (
                "Expert mode is off: help, shell completion and the web app list the everyday commands; every other "
                "command still runs when you type it."
            ),
            "List every command: hz system expert on",
        ]
    if result.note:
        lines.append(result.note)
    return Text("\n".join(lines))


@module.operation(render=render, audience="everyday")
def expert(service: ExpertService) -> ExpertMode:
    """Show whether expert mode is on: `on` lists every command, `off` just the everyday ones."""
    return service.mode()


@module.operation(read_only=False, render=render, audience="everyday")
def on(service: ExpertService) -> ExpertMode:
    """Turn expert mode on: help, completion and the web app list every command."""
    return service.change(expert=True)


@module.operation(read_only=False, render=render, audience="everyday")
def off(service: ExpertService) -> ExpertMode:
    """Turn expert mode off: help, completion and the web app list the everyday commands."""
    return service.change(expert=False)


__all__ = ["ExpertMode", "ExpertService", "OperationAudience", "module"]
