"""Operation discovery at the kernel boundary shared by all heads."""

from typing import Protocol

from heinzel.kernel._kernel import ModuleRecord
from heinzel.kernel._lets import Let
from heinzel.spec._kernel_view import KernelView


class HeadKernel(KernelView, Protocol):
    """Extend the service boundary with the existing operation registry."""

    def lets[L: Let](self, kind: type[L]) -> list[tuple[ModuleRecord, L]]:
        """List matching declarations in module order."""
        ...

    def children(self, qualname: str | None) -> tuple[ModuleRecord, ...]:
        """List the registry's direct children."""
        ...
