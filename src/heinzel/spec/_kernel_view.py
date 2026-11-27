"""The service and call boundary a head uses instead of construction internals."""

from collections.abc import Callable
from typing import Any, Protocol


class KernelView(Protocol):
    """Services and injection shared by the CLI, API, MCP and web heads."""

    def provides(self, kind: object) -> bool:
        """Whether the annotation identifies an injectable service."""
        ...

    def bind[T](self, kind: type[T], instance: T) -> None:
        """Bind a head-owned instance for injection."""
        ...

    def available(self, func: Callable[..., Any]) -> bool:
        """Whether the dependencies of a callable are available."""
        ...

    def resolve[T](self, kind: type[T]) -> T:
        """Return the service for this injectable type."""
        ...

    def call[R](self, func: Callable[..., R], /, **kwargs: Any) -> R:  # noqa: ANN401 - caller-supplied arguments
        """Run a callable with its remaining arguments injected."""
        ...
