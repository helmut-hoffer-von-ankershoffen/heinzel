"""A fake newer release for the automatic-update end-to-end test (ADR 0116); nothing is installed, nothing is fetched.

Python imports this file at start when its folder is on PYTHONPATH, so the fakes hold in `hz ui` and in the process a
restart in place becomes. They are active only when HZ_E2E_FAKE_RELEASE names a folder:

- once the test writes `release` there, the newest stable release is 99.0.0 and a plugin has 9.0.0, until an update
  "installs" them; the server checks every two seconds;
- `system.update` writes `applied` in that folder instead of running uv, and reports the running version;
- the server's clock runs ahead by the seconds in `clock` there, so the test can skip a postponed hour;
- the installation counts as a uv tool on a release tag, whose environment is the folder `env` there.
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

FOLDER = os.environ.get("HZ_E2E_FAKE_RELEASE")

if FOLDER:
    from heinzel import __version__
    from heinzel.heads.web import _auto_update
    from heinzel.heads.web._rollback import ToolSnapshot
    from heinzel.kernel import CorePin, Installation
    from heinzel.modules.system import _plugin_releases, _service, _update
    from heinzel.modules.system._models import UpdateResult

    folder = Path(FOLDER)
    applied = folder / "applied"

    def released() -> bool:
        return (folder / "release").exists() and not applied.exists()

    def latest_version(self: object, timeout: float) -> str:
        return "99.0.0" if released() else __version__

    def newer(self: object, timeout: float) -> dict[str, str]:
        return {"heinzel-example": "9.0.0"} if released() else {}

    def update(self: object, *args: object, **kwargs: object) -> UpdateResult:
        applied.write_text("fake update applied\n", encoding="utf-8")
        return UpdateResult(
            before=__version__,
            after=__version__,
            latest=None,
            pinned_to=None,
            updated=True,
            message="Fake update for the end-to-end test.",
            next=[],
        )

    def now() -> datetime:
        try:
            ahead = float((folder / "clock").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ahead = 0.0
        return datetime.now(UTC) + timedelta(seconds=ahead)

    def core_pin(self: object) -> CorePin:
        return CorePin("release", "heinzel", ref="v0.1.0")

    _service.GitHubReleases.latest_version = latest_version  # type: ignore[method-assign]
    _plugin_releases.PyPIPluginReleases.newer = newer  # type: ignore[method-assign]
    _update.UpdateService.update = update  # type: ignore[method-assign]
    _auto_update._now = now
    _auto_update.FIRST_CHECK = (timedelta(seconds=1), timedelta(seconds=2))
    _auto_update.CHECK_EVERY = timedelta(seconds=2)
    _auto_update.JITTER = timedelta(0)

    def take_snapshot(self: object) -> ToolSnapshot:
        (folder / "env").mkdir(exist_ok=True)
        return ToolSnapshot.take(folder / "env", folder / "env-before-update")

    Installation.core_pin = core_pin  # type: ignore[method-assign]
    _auto_update.AutoUpdater._take_snapshot = take_snapshot  # type: ignore[method-assign]
