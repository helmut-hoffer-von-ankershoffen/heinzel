"""Offline installation maintenance port; implementation and service authority belong to the system module."""

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from heinzel.kernel._services import ServiceContract


class UninstallResult(BaseModel):
    """Observed removal, never a promise that user data or other applications were deleted."""

    removed: Literal[True] = Field(
        default=True, description="The verified tool environment and its receipt-owned scripts were observed removed."
    )
    data_retained: Literal[True] = Field(default=True, description="User data and configuration were retained.")
    skills_retained: Literal[True] = Field(
        default=True, description="Agent skills outside the tool environment were retained."
    )
    notifier_retained: Literal[True] = Field(
        default=True, description="The separately installed notifier was retained."
    )
    daemon_removed: bool = Field(
        description="Owned daemon definitions were removed after verified stop and tool removal."
    )
    web_removed: bool = Field(description="Owned web integration was removed after verified stop and tool removal.")
    message: str = Field(
        default="Heinzel was uninstalled. Your data, configuration, agent skills and notifier were kept.",
        description="The observed removal and explicit retained-resource scope.",
    )


class OfflineUninstall(ServiceContract):
    """Explicit local removal of one verified standard installation, preserving user work.

    The CLI host resolves the exact built-in system implementation through registry provenance. This port does
    not discover providers, inspect credentials, install packages or grant authority to a rebound callback.
    """

    def uninstall(self, *, on_recovery: Callable[[str], None] | None = None) -> UninstallResult:
        """Remove only the verified installation, reporting a usable recovery command before effects."""
        raise NotImplementedError
