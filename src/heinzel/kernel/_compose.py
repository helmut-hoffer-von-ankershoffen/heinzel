"""Compose the kernel's built-in service instances and lazy factories."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from heinzel.kernel._config import KERNEL_SECTION, Config
from heinzel.kernel._identity import CurrentUser
from heinzel.kernel._local_web import LocalWebRuntime
from heinzel.kernel._progress import Progress
from heinzel.kernel._prompter import Prompter
from heinzel.kernel._settings import Settings
from heinzel.kernel.install._installation import Installation


@dataclass(frozen=True, slots=True)
class ServiceSet:
    """Services supplied by the host, with injectable types as keys.

    Instances take precedence over factories. Factories use the kernel's normal
    dependency injection and caching. Each kernel copies the supplied mappings.
    """

    instances: Mapping[type[object], object] = field(default_factory=dict)
    factories: Mapping[type[object], Callable[..., object]] = field(default_factory=dict)


def _current_user() -> CurrentUser:
    """Build identity from trusted user settings only when a head actually needs it."""
    home = Path.home()
    state = Config.load(home).settings(Settings, KERNEL_SECTION).state_dir.expanduser()
    # Relative user settings must not implicitly select the repository a head happened to start in.
    return CurrentUser((state if state.is_absolute() else home / state) / "identity")


def default_services(overrides: ServiceSet) -> ServiceSet:
    """Create fresh per-kernel services, without building overridden defaults."""
    eager: dict[type[object], Callable[[], object]] = {
        Progress: Progress,
        LocalWebRuntime: LocalWebRuntime,
        Prompter: Prompter,
    }
    supplied = overrides.instances.keys() | overrides.factories.keys()
    instances = {kind: create() for kind, create in eager.items() if kind not in supplied}
    factories: dict[type[object], Callable[..., object]] = {
        Installation: Installation,
        CurrentUser: _current_user,
    }
    return ServiceSet(instances, factories)
