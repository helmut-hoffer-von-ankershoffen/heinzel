"""Turn an `OperationLet` into a Typer command."""

import inspect
from collections.abc import Callable
from datetime import date, datetime, time
from enum import StrEnum
from types import UnionType
from typing import Annotated, Any, get_args, get_origin

import typer
from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo
from rich.console import Console
from rich.text import Text

from heinzel.heads.cli import _playful
from heinzel.heads.cli._progress import ConsoleProgress
from heinzel.heads.cli._render import TerminalSafe, message, show, to_json
from heinzel.heads.cli._setup import ConsolePrompter, has_terminal
from heinzel.kernel import CurrentUser, HeinzelError, Kernel, OperationLet, Prompter

FORMAT_PARAMETER = "output_format"
JSON_PARAMETER = "json_output"


class Format(StrEnum):
    """How a command prints its result."""

    TEXT = "text"
    JSON = "json"


def command(  # noqa: PLR0913 - the head's collaborators, plus the optional prologue and epilogue
    kernel: Kernel,
    operation: OperationLet,
    console: Console,
    err_console: Console,
    progress: ConsoleProgress,
    *,
    prologue: Callable[[object], None] | None = None,
    epilogue: Callable[[], None] | None = None,
) -> Callable[..., None]:
    """A Typer-compatible callable that runs the operation through the kernel and presents its result.

    The command's signature is the operation's, minus injected parameters, plus `--format` and its shorthand `--json`.
    Parameters without a default become arguments, the others options; `Field(description=...)` becomes their help
    text. Progress goes to stderr and is silent for JSON output, so stdout always holds just the result.
    `prologue` runs before text output, with the result, and `epilogue` after it; neither runs for JSON.
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
        # On a terminal, and not for scripts (--json), operations that ask (a plugin's preview) may ask.
        kernel.bind(Prompter, ConsolePrompter(console) if has_terminal() and not as_json else Prompter())
        try:
            with kernel.resolve(CurrentUser).local("cli"):
                result = kernel.invoke(operation, **kwargs)
        except HeinzelError as exc:
            if not as_json and _playful.allowed(kernel.settings, err_console):
                err_console.print(_playful.glyph(), end=" ")
            err_console.print(message("Error:", str(exc)), highlight=False, soft_wrap=True)
            raise typer.Exit(code=1) from exc
        except OSError as exc:
            err_console.print(message("Error:", str(exc)), highlight=False, soft_wrap=True)
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            if kernel.settings.log_level == "DEBUG":
                raise
            err_console.print(
                message(
                    "Unexpected error:",
                    f"{type(exc).__name__}: {exc}\nThis is a bug. Rerun with HZ_LOG_LEVEL=DEBUG for the traceback "
                    "and report it with `hz system doctor --json`.",
                ),
                highlight=False,
            )
            raise typer.Exit(code=1) from exc
        if as_json:
            console.print(to_json(result), markup=False, highlight=False, soft_wrap=True)  # JSON escapes controls
        else:
            _present(console, operation, result, prologue, epilogue)
        if operation.exit_code is not None and (code := operation.exit_code(result)):
            raise typer.Exit(code=code)

    run.__signature__ = inspect.Signature([*parameters, format_option, json_option])  # type: ignore[attr-defined]
    run.__doc__ = inspect.getdoc(operation.func)
    run.__name__ = operation.name
    return run


def _present(
    console: Console,
    operation: OperationLet,
    result: object,
    prologue: Callable[[object], None] | None,
    epilogue: Callable[[], None] | None,
) -> None:
    """Print a result as text, with the operation's renderer if it has one, between prologue and epilogue."""
    if prologue is not None:
        prologue(result)
    if operation.render is not None:
        rendered = operation.render(result)
        # Bare text is content to copy or pipe as it is (a draft, a flowchart): the terminal wraps it, Rich must not
        # break its lines. Tables and groups are laid out for the terminal's width.
        console.print(TerminalSafe(rendered), soft_wrap=isinstance(rendered, Text))
    else:
        show(console, result)
    if epilogue is not None:
        epilogue()


def _cli_parameter(parameter: inspect.Parameter) -> inspect.Parameter:
    annotation = parameter.annotation
    description = None
    if get_origin(annotation) is Annotated:
        annotation, *metadata = get_args(annotation)
        description = next((m.description for m in metadata if isinstance(m, FieldInfo)), None)
    parser = _json_parser(annotation) if _structured(annotation) else None
    info = (
        typer.Argument(help=description, metavar=_metavar(annotation), parser=parser)
        if parameter.default is inspect.Parameter.empty
        else typer.Option(help=description, metavar=_metavar(annotation), parser=parser)
    )
    return parameter.replace(annotation=Annotated[_as_text(annotation), info], kind=inspect.Parameter.KEYWORD_ONLY)


TEMPORAL = (datetime, date, time)


def _structured(annotation: Any) -> bool:  # noqa: ANN401 - any public operation annotation
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return True
    origin = get_origin(annotation)
    if origin is dict:
        return True
    if origin is UnionType or origin is list:
        return any(_structured(part) for part in get_args(annotation))
    return False


def _json_parser(annotation: Any) -> Callable[[str], Any]:  # noqa: ANN401 - returns the operation's validated model
    adapter = TypeAdapter[Any](annotation)

    def parse(value: str) -> Any:  # noqa: ANN401 - generic public parameter model
        try:
            return adapter.validate_json(value)
        except ValidationError:
            msg = "Expected JSON matching this operation's parameter schema"
            raise typer.BadParameter(msg) from None

    return parse


def _as_text(annotation: Any) -> Any:  # noqa: ANN401 - any annotation
    """Temporal text and structured JSON use the same Pydantic types as the API and MCP heads."""
    if _structured(annotation):
        return str | None if type(None) in get_args(annotation) else str
    if _temporal(annotation):
        return str
    if isinstance(annotation, UnionType) and any(map(_temporal, get_args(annotation))):
        return str | None
    return annotation


def _temporal(annotation: object) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, TEMPORAL)


def _metavar(annotation: Any) -> str | None:  # noqa: ANN401 - any annotation
    """`ISO-8601` for dates and times; Typer's default otherwise."""
    if _structured(annotation):
        return "JSON"
    if _as_text(annotation) is not annotation:
        return "ISO-8601"
    return None
