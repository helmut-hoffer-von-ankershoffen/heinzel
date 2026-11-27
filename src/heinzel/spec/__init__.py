"""Declarative Heinzel types."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from heinzel.spec._kernel_view import KernelView as KernelView

__all__ = ["KernelView"]


def __getattr__(name: str) -> object:
    """Load the head protocol only when a caller asks for it.

    Raises:
        AttributeError: If the name is not exported by this package.
    """
    if name == "KernelView":
        from heinzel.spec._kernel_view import KernelView  # noqa: PLC0415 - keep spec package initialization inert

        return KernelView
    raise AttributeError(name)
