"""Turn an `OperationLet` into a Typer command."""

import inspect
from collections.abc import Callable
from datetime import date, datetime, time
from enum import StrEnum
from types import UnionType
from typing import Annotated, Any, get_args, get_origin

import typer
from pydantic.fields import FieldInfo
from rich.console import Console

from heinzel.heads.cli._progress import ConsoleProgress
from heinzel.heads.cli._render import show, to_json
from heinzel.kernel import HeinzelError, Kernel, OperationLet

FORMAT_PARAMETER = "output_format"
JSON_PARAMETER = "json_output"


class Format(StrEnum):
    """How a command prints its result."""

    TEXT = "text"
    JSON = "json"


def command(  # noqa: PLR0913 - the head's collaborators, plus the optional epilogue
    kernel: Kernel,
    operation: OperationLet,
    console: Console,
    err_console: Console,
    progress: ConsoleProgress,
    *,
    epilogue: Callable[[], None] | None = None,
) -> Callable[..., None]:
    """A Typer-compatible callable that runs the operation through the kernel and presents its result.

    The command's signature is the operation's, minus injected parameters, plus `--format` and its shorthand `--json`.
    Parameters without a default become arguments, the others options; `Field(description=...)` becomes their help
    text. Progress goes to stderr and is silent for JSON output, so stdout always holds just the result.
    `epilogue` runs after text output only, never after JSON.
    """
    parameters = [
        _cli_parameter(parameter)
        for parameter in operation.signature().parameters.values()
        if not kernel.provides(get_origin(parameter.annotation) or parameter.annotation)
    ]
    format_option = inspect.Parameter(
        FORMAT_PARAMETER,
        inspect.Parameter.KEYWORD_ONLY,
        default=None,
        annotation=Annotated[
            Format | None,
            typer.Option(
                "--format",
                help="Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.",
                case_sensitive=False,
            ),
        ],
    )
    json_option = inspect.Parameter(
        JSON_PARAMETER,
        inspect.Parameter.KEYWORD_ONLY,
        default=False,
        annotation=Annotated[bool, typer.Option("--json", help="Shorthand for --format json, for scripts and agents.")],
    )

    def run(**kwargs: Any) -> None:  # noqa: ANN401 - forwarded to the operation
        chosen = kwargs.pop(FORMAT_PARAMETER)
        as_json = kwargs.pop(JSON_PARAMETER) or (chosen or Format(kernel.settings.output_format)) is Format.JSON
        progress.enabled = not as_json
        try:
            result = kernel.invoke(operation, **kwargs)
        except HeinzelError as exc:
            err_console.print(f"[red]Error:[/red] {exc}", highlight=False, soft_wrap=True)
            raise typer.Exit(code=1) from exc
        except OSError as exc:
            err_console.print(f"[red]Error:[/red] {exc}", highlight=False, soft_wrap=True)
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            if kernel.settings.log_level == "DEBUG":
                raise
            err_console.print(
                f"[red]Unexpected error:[/red] {type(exc).__name__}: {exc}\n"
                "This is a bug. Rerun with HZ_LOG_LEVEL=DEBUG for the traceback and report it with "
                "`hz system doctor --json`.",
                highlight=False,
            )
            raise typer.Exit(code=1) from exc
        if as_json:
            console.print(to_json(result), markup=False, highlight=False, soft_wrap=True)
        elif operation.render is not None:
            console.print(operation.render(result))
        else:
            show(console, result)
        if not as_json and epilogue is not None:
            epilogue()
        if operation.exit_code is not None and (code := operation.exit_code(result)):
            raise typer.Exit(code=code)

    run.__signature__ = inspect.Signature([*parameters, format_option, json_option])  # type: ignore[attr-defined]
    run.__doc__ = inspect.getdoc(operation.func)
    run.__name__ = operation.name
    return run


def _cli_parameter(parameter: inspect.Parameter) -> inspect.Parameter:
    annotation = parameter.annotation
    description = None
    if get_origin(annotation) is Annotated:
        annotation, *metadata = get_args(annotation)
        description = next((m.description for m in metadata if isinstance(m, FieldInfo)), None)
    info = (
        typer.Argument(help=description, metavar=_metavar(annotation))
        if parameter.default is inspect.Parameter.empty
        else typer.Option(help=description, metavar=_metavar(annotation))
    )
    return parameter.replace(annotation=Annotated[_as_text(annotation), info], kind=inspect.Parameter.KEYWORD_ONLY)


TEMPORAL = (datetime, date, time)


def _as_text(annotation: Any) -> Any:  # noqa: ANN401 - any annotation
    """Dates and times reach the kernel as text, where pydantic parses ISO 8601 (with `Z`) like every other head."""
    if _temporal(annotation):
        return str
    if isinstance(annotation, UnionType) and any(map(_temporal, get_args(annotation))):
        return str | None
    return annotation


def _temporal(annotation: object) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, TEMPORAL)


def _metavar(annotation: Any) -> str | None:  # noqa: ANN401 - any annotation
    """`ISO-8601` for dates and times; Typer's default otherwise."""
    if _as_text(annotation) is not annotation:
        return "ISO-8601"
    return None
