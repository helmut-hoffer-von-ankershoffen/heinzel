"""When a notice resolves by itself: its `ResolveWhen`, evaluated here, never by a head (ADR 0043, amended 2026-11-07).

A resolved notice disappears from the list, Today and the unread count; it stays in the store with when and why, as
history. Resolving never marks a notice read: only the person does that.

Release notices recorded before conditions existed carry none; their key (`release:<version>`) implies one, so a stale
"hz X is available" disappears once X is installed, whichever process recorded it.
"""

import logging
from collections.abc import Callable
from importlib import metadata

from packaging.version import InvalidVersion, Version

from heinzel import CapabilityState, HeinzelError, Kernel, Notice, NoticeKind, OperationLet, ResolveWhen, __version__

logger = logging.getLogger(__name__)
RELEASE_KEY = "release:"
"""The key prefix of a release notice; the rest is the announced version."""

CapabilityProbe = Callable[[CapabilityState], bool | None]
"""Whether a capability is in the state named; None when it can't tell, which never resolves a notice."""


def installed_version() -> str:
    """The hz installed on this machine, read from disk now, so a process started before an update still knows."""
    try:
        return metadata.version("heinzel")
    except metadata.PackageNotFoundError:
        return __version__


def _at_least(installed: str, wanted: str) -> bool:
    try:
        return Version(installed) >= Version(wanted)
    except InvalidVersion:
        return False


def condition(notice: Notice) -> ResolveWhen | None:
    """The notice's condition: its own, or the one a legacy release notice's key implies; None for none."""
    if notice.resolve_when is not None:
        return notice.resolve_when
    if notice.kind is NoticeKind.RELEASE and notice.key.startswith(RELEASE_KEY):
        try:
            return ResolveWhen(installed_version_at_least=notice.key.removeprefix(RELEASE_KEY))
        except ValueError:
            return None
    return None


def holds(when: ResolveWhen, installed: str, probe: CapabilityProbe | None) -> str | None:
    """Why `when` holds now, in words; None while it doesn't, or when a capability can't be asked (`probe` None)."""
    reasons: list[str] = []
    if (wanted := when.installed_version_at_least) is not None:
        if not _at_least(installed, wanted):
            return None
        reasons.append(f"hz {installed} is installed")
    if (capability := when.capability) is not None:
        if probe is None or probe(capability) is not True:
            return None
        reasons.append(f"{capability.operation} reports {capability.field} = {capability.equals!r}")
    return "; ".join(reasons) or None


def cached(probe: CapabilityProbe) -> CapabilityProbe:
    """`probe`, asking each capability once per pass."""
    answers: dict[CapabilityState, bool | None] = {}

    def ask(capability: CapabilityState) -> bool | None:
        if capability not in answers:
            answers[capability] = probe(capability)
        return answers[capability]

    return ask


def kernel_probe(kernel: Kernel) -> CapabilityProbe:
    """Ask a capability through a read-only operation of its module, by name: the core imports no plugin (ADR 0094)."""

    def probe(capability: CapabilityState) -> bool | None:
        qualname, name = capability.operation.split(":", 1)
        found = next(
            (let for owner, let in kernel.lets(OperationLet) if (owner.qualname, let.name) == (qualname, name)), None
        )
        if found is None or not found.read_only or not kernel.available(found.func):
            return None
        try:
            result = kernel.invoke(found)
        except HeinzelError:
            return None
        except Exception:
            logger.warning("asking %s for a notice's condition failed", capability.operation, exc_info=True)
            return None
        if not hasattr(result, capability.field):
            return None
        value: object = getattr(result, capability.field)
        return type(value) is type(capability.equals) and value == capability.equals

    return probe
