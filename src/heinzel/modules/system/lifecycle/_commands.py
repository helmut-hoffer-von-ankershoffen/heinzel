"""Pre-kernel command ownership; only a verified active deployment supplies executable code."""

from dataclasses import dataclass
from pathlib import Path

from pydantic import UUID4, Field

from heinzel import HeinzelError
from heinzel.modules.system.lifecycle._models import Activation, Channel, Name, Record, RuntimeManifest
from heinzel.modules.system.lifecycle._store import LifecycleStore, private_directory, regular_bytes
from heinzel.modules.system.lifecycle._writers import WriterLease, writer_lease


class ProfileChoice(Record):
    """Closed native profile/channel selection, never an arbitrary filesystem path."""

    name: Name
    channel: Channel


class ProfileIdentity(ProfileChoice):
    """The same immutable owner/profile identity created by the native shell."""

    version: int = Field(strict=True, ge=1, le=1)
    id: UUID4
    owner: int = Field(strict=True, ge=0)


@dataclass(frozen=True, slots=True)
class CommandClaim:
    """Bind a command's entire lifetime to one verified activation and its shared writer descriptor."""

    activation: Activation
    manifest: RuntimeManifest
    lease: WriterLease


def profile_directory(root: Path, name: str, channel: Channel, owner: int) -> Path | None:
    """Read an existing native identity without creating state or accepting a caller-selected profile path.

    Raises:
        HeinzelError: If any existing profile component or identity is unsafe or belongs to another owner.
    """
    try:
        # Validate names before joining; model errors are presented by the bootstrap isolation boundary.
        expected = ProfileChoice(name=name, channel=channel)
        directory = root / channel / name
        for path in (root, root / channel, directory):
            try:
                path.lstat()
            except FileNotFoundError:
                return None
            private_directory(path)
        identity = ProfileIdentity.model_validate_json(regular_bytes(directory / "profile.json", limit=4096))
    except (OSError, ValueError):
        msg = "The desktop profile identity is unavailable or changed; repair it before running commands."
        raise HeinzelError(msg) from None
    if identity.owner != owner or identity.name != expected.name or identity.channel != expected.channel:
        msg = "The desktop profile identity changed; repair it before running commands."
        raise HeinzelError(msg)
    return directory


def claim_command(store: LifecycleStore) -> CommandClaim:
    """Verify and acquire the active generation while holding the one transition authority.

    A prepared plan blocks new claims, while an earlier claimant keeps its inherited shared descriptor until
    every executing process exits. This function never discovers plugins or calls the kernel boot function.

    Raises:
        HeinzelError: If the profile is draining, inactive, changed or has an unsafe writer barrier.
    """
    with store.lock():
        if store.pending() is not None:
            msg = (
                "This desktop profile is draining or needs transition reconciliation; finish or cancel its exact plan."
            )
            raise HeinzelError(msg)
        activation = store.active()
        if activation is None:
            msg = "This desktop profile has no active runtime; repair its managed installation before running commands."
            raise HeinzelError(msg)
        _, manifest = store.verify(activation.deployment)
        return CommandClaim(activation, manifest, writer_lease(store.directory))
