"""A private, comparable snapshot of the owning runner module's current configuration."""

from dataclasses import dataclass, field

from heinzel.kernel._config import Config
from heinzel.kernel._errors import HeinzelError


@dataclass(frozen=True, slots=True)
class RunnerSnapshot:
    """The selected executable/model and an opaque full-configuration change detector."""

    claude_executable: str
    model: str | None
    revision: str = field(repr=False)


class RunnerSnapshotProvider:
    """Host service implemented by the runner owner, never by the model or request payload."""

    def snapshot(self, _config: Config) -> RunnerSnapshot:
        """Refuse when the owning runner module has not supplied its implementation.

        Raises:
            HeinzelError: Always for this base service.
        """
        msg = "The configured runner snapshot is unavailable"
        raise HeinzelError(msg)
