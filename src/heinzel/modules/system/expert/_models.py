"""Models of the expert module."""

from typing import Literal

from pydantic import BaseModel, Field


class OperationAudience(BaseModel):
    """Who one operation is for."""

    module: str = Field(description="The qualified name of the operation's module, e.g. `workflow` or `system.doctor`.")
    operation: str = Field(description="The operation's name within its module, e.g. `graph`.")
    audience: Literal["everyday", "expert"] = Field(
        description="`everyday`: always listed; `expert`: listed in expert mode only. Both run when called."
    )


class ExpertMode(BaseModel):
    """Whether expert mode is on, and who each operation is for."""

    expert: bool = Field(description="Whether help, completion and the web app list every command.")
    operations: list[OperationAudience] = Field(
        description="Every available operation with its audience, so other heads list the same ones the CLI does."
    )
    note: str | None = Field(default=None, description="Why the mode in effect differs from the one asked for.")
    next: list[str] = Field(default_factory=list[str], description="What to run next.")
