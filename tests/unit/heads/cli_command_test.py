import inspect
from typing import Annotated, get_args

import pytest
import typer
from pydantic import Field
from rich.console import Console

from heinzel import Kernel, Module, OperationLet
from heinzel.heads.cli._command import _cli_parameter, command
from heinzel.heads.cli._progress import ConsoleProgress

pytestmark = pytest.mark.unit


def _parameter(annotation, default=inspect.Parameter.empty):
    return inspect.Parameter("p", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation, default=default)


def test_required_parameters_become_arguments_with_field_description():
    converted = _cli_parameter(_parameter(Annotated[str, Field(description="Who.")]))
    base, info = get_args(converted.annotation)
    assert base is str
    assert isinstance(info, typer.models.ArgumentInfo)
    assert info.help == "Who."


def test_parameters_with_defaults_become_options():
    converted = _cli_parameter(_parameter(int, default=3))
    _, info = get_args(converted.annotation)
    assert isinstance(info, typer.models.OptionInfo)
    assert converted.default == 3


def test_command_hides_injected_parameters_and_adds_json(make_kernel):
    def operation(name: str, kernel: Kernel) -> str:
        """Do it."""
        return name

    kernel = make_kernel(x=Module("x", "x"))
    operation_let = OperationLet(name="op", func=operation, summary="Do it.")
    run = command(kernel, operation_let, Console(), Console(), ConsoleProgress(Console()))
    assert list(inspect.signature(run).parameters) == ["name", "output_format", "json_output"]
    assert run.__doc__ == "Do it."
