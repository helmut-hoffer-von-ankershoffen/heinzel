"""A submodule that collapses into `hz dummy greet`."""

from typing import Annotated

from pydantic import Field

from heinzel import Module

module = Module("greet", "Greet someone.")


@module.operation
def greet(name: Annotated[str, Field(description="Who to greet.")] = "world") -> str:
    """Greet someone."""
    return f"Hello, {name}!"
