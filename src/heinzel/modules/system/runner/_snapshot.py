"""The runner's host-derived, private configuration change detector."""

import hashlib
import json

from heinzel import Config
from heinzel.kernel import RunnerSnapshot, RunnerSnapshotProvider
from heinzel.modules.system.runner._models import RunnerSettings


class RunnerSnapshotService(RunnerSnapshotProvider):
    """Derive the exact runner settings through the same trusted configuration rules as the runner."""

    def snapshot(self, config: Config) -> RunnerSnapshot:
        settings = config.settings(RunnerSettings, "system.runner")
        data = json.dumps(settings.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return RunnerSnapshot(settings.claude_executable, settings.model, hashlib.sha256(data.encode()).hexdigest())
