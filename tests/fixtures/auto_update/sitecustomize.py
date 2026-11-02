"""A fake newer release for the automatic-update end-to-end test (ADR 0116); nothing is installed, nothing is fetched.

Python imports this file at start when its folder is on PYTHONPATH, so the fakes hold in `hz ui` and in the process a
restart in place becomes. They are active only when HZ_E2E_FAKE_RELEASE names a folder:

- once the test writes `release` there, the newest stable release is 99.0.0 and a plugin has 9.0.0, until an update
  "installs" them; the server checks every two seconds;
- `system.apply_announced` writes `applied` in that folder instead of running uv, then starts this hz as its trial;
- the server's clock runs ahead by the seconds in `clock` there, so the test can skip a postponed hour;
- the installation counts as a uv tool on a release tag.
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

FOLDER = os.environ.get("HZ_E2E_FAKE_RELEASE")

if FOLDER:
    from heinzel import __version__
    from heinzel.heads.web import _auto_update
    from heinzel.kernel import CorePin, Installation, PluginRelease, ReleaseFacts
    from heinzel.modules.system import _plugin_releases, _service, _update
    from heinzel.modules.system._models import AnnouncedUpdate

    folder = Path(FOLDER)
    LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)  # old enough for any minimum age
    applied = folder / "applied"

    def released() -> bool:
        return (folder / "release").exists() and not applied.exists()

    def latest_version(self: object, timeout: float) -> str:
        return "99.0.0" if released() else __version__

    def newer(self: object, timeout: float) -> dict[str, str]:
        return {"heinzel-example": "9.0.0"} if released() else {}

    def newer_releases(self: object, timeout: float) -> dict[str, PluginRelease]:
        return {name: PluginRelease(version, LONG_AGO) for name, version in newer(self, timeout).items()}

    def release_facts(self: object, version: str, timeout: float) -> ReleaseFacts:
        return ReleaseFacts(version, LONG_AGO, None)

    def release(self: object, name: str, version: str, timeout: float) -> PluginRelease:
        return PluginRelease(version, LONG_AGO)

    def apply_announced(self: object, announced: Any, **kwargs: Any) -> AnnouncedUpdate:
        """Instead of uv: record what would be installed, then the real trial start of this hz."""
        what = f"{announced.core} {dict(announced.plugins)}"
        applied.write_text(f"fake update applied: {what}\n", encoding="utf-8")
        started = kwargs["trial"](__version__)
        return AnnouncedUpdate(
            updated=started, after=__version__, restored=not started, refused=not started, message="Fake update."
        )

    def now() -> datetime:
        try:
            ahead = float((folder / "clock").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ahead = 0.0
        return datetime.now(UTC) + timedelta(seconds=ahead)

    def core_pin(self: object) -> CorePin:
        return CorePin("release", "heinzel", ref="v0.1.0", git="https://github.com/org/heinzel")

    _service.GitHubReleases.latest_version = latest_version  # type: ignore[method-assign]
    _plugin_releases.PyPIPluginReleases.newer = newer  # type: ignore[method-assign]
    _plugin_releases.PyPIPluginReleases.newer_releases = newer_releases  # type: ignore[method-assign]
    _service.GitHubReleases.release_facts = release_facts  # type: ignore[method-assign]
    _update.UpdateService.apply_announced = apply_announced  # type: ignore[method-assign]
    _plugin_releases.PyPIPluginReleases.release = release  # type: ignore[method-assign]
    _auto_update._now = now
    _auto_update.FIRST_CHECK = (timedelta(seconds=1), timedelta(seconds=2))
    _auto_update.CHECK_EVERY = timedelta(seconds=2)
    _auto_update.JITTER = timedelta(0)

    Installation.core_pin = core_pin  # type: ignore[method-assign]
