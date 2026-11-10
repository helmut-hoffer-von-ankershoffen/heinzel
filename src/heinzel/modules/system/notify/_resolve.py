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

DOCTOR_KEY = "doctor:"
"""The key prefix of a doctor notice: `doctor:<status>:<check>|<check>…`."""

CapabilityProbe = Callable[[CapabilityState], bool | None]
"""Whether a capability is in the state named; None when it can't tell, which never resolves a notice."""
ProblemsProbe = Callable[[], set[str] | None]
"""The health checks that aren't up now (`<module>: <check>`); None when doctor can't tell, which resolves nothing."""


def installed_version() -> str:
    """The hz installed on this machine, read from disk now, so a process started before an update still knows."""
    try:
        return metadata.version("heinzel")
    except metadata.PackageNotFoundError:
        return __version__


def plugin_installed(distribution: str) -> bool:
    """Whether a plugin distribution is installed on this machine, read from disk now."""
    try:
        metadata.distribution(distribution)
    except metadata.PackageNotFoundError:
        return False
    return True


def _at_least(installed: str, wanted: str) -> bool:
    try:
        return Version(installed) >= Version(wanted)
    except InvalidVersion:
        return False


def condition(notice: Notice) -> ResolveWhen | None:
    """The notice's condition: its own, or the one a legacy release or doctor notice's key implies; None for none.

    So notices recorded before conditions existed resolve too, without a sweep.
    """
    if notice.resolve_when is not None:
        return notice.resolve_when
    try:
        if notice.kind is NoticeKind.RELEASE and notice.key.startswith(RELEASE_KEY):
            return ResolveWhen(installed_version_at_least=notice.key.removeprefix(RELEASE_KEY))
        if notice.kind is NoticeKind.DOCTOR and notice.key.startswith(DOCTOR_KEY):
            _, _, checks = notice.key.removeprefix(DOCTOR_KEY).partition(":")
            return ResolveWhen(checks_up=checks.split("|")) if checks else None
    except ValueError:
        return None
    return None


def at_read(when: ResolveWhen) -> bool:
    """Whether `when` can be evaluated from installed metadata without running another operation."""
    return when.capability is None and when.checks_up is None


def holds(
    when: ResolveWhen, installed: str, probe: CapabilityProbe | None, problems: ProblemsProbe | None = None
) -> str | None:
    """Why `when` holds now, in words; None while it doesn't, or when what it names can't be asked (a probe None)."""
    reasons: list[str] = []
    if (wanted := when.installed_version_at_least) is not None:
        if not _at_least(installed, wanted):
            return None
        reasons.append(f"hz {installed} is installed")
    if (capability := when.capability) is not None:
        # Unrecognised or damaged on-disk envelopes must never resolve via a capability probe.
        if capability.operation.startswith("system.notify:condition_v"):
            return None
        if probe is None or probe(capability) is not True:
            return None
        reasons.append(f"{capability.operation} reports {capability.field} = {capability.equals!r}")
    if (plugins := when.plugins_installed) is not None:
        if not all(plugin_installed(name) for name in plugins):
            return None
        reasons.append(f"{', '.join(plugins)} {'is' if len(plugins) == 1 else 'are'} installed")
    if (checks := when.checks_up) is not None:
        failing = problems() if problems is not None else None
        # Doctor represents an exception from a provider as `<module>: checks` DOWN.  Its individual
        # checks are unknown, not UP, until the provider answers again.
        provider_failures = {f"{check.partition(': ')[0]}: checks" for check in checks}
        if failing is None or failing & (set(checks) | provider_failures):
            return None
        reasons.append(f"{', '.join(checks)} {'is' if len(checks) == 1 else 'are'} up again")
    return "; ".join(reasons) or None


def once(problems: ProblemsProbe) -> ProblemsProbe:
    """`problems`, asking doctor at most once per pass."""
    answer: list[set[str] | None] = []

    def ask() -> set[str] | None:
        if not answer:
            answer.append(problems())
        return answer[0]

    return ask


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
