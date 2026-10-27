"""Shared desktop installation and activation authority (ADR 0078)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from heinzel.modules.system.lifecycle._commands import CommandClaim
    from heinzel.modules.system.lifecycle._models import Channel
    from heinzel.modules.system.lifecycle._store import LifecycleStore

__all__ = ["claim_command", "profile_directory"]


def claim_command(store: LifecycleStore) -> CommandClaim:
    """Claim a managed generation before kernel discovery on a supported desktop platform.

    Raises:
        HeinzelError: If runtime, draining or writer ownership cannot be established.
    """
    from heinzel.modules.system.lifecycle._commands import claim_command as claim  # noqa: PLC0415 - POSIX bootstrap only

    return claim(store)


def profile_directory(root: Path, name: str, channel: Channel, owner: int) -> Path | None:
    """Resolve an existing checked native identity without creating a profile.

    Raises:
        HeinzelError: If existing profile metadata is unsafe or inconsistent.
    """
    from heinzel.modules.system.lifecycle._commands import profile_directory as locate  # noqa: PLC0415 - POSIX bootstrap only

    return locate(root, name, channel, owner)
