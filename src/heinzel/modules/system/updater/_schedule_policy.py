"""When a server may update itself at all: source checkouts, development runs, pins and switched-off checks."""

import importlib.metadata
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from heinzel.kernel import Settings
from heinzel.modules.system.updater._schedule_plan import Plan

SOURCE_CHECKOUT = "Updates are managed by your source checkout."
"""What both switches say when hz runs from a source checkout or an editable install."""
DEVELOPMENT = "Automatic updates are off while hz runs for development or tests."
"""What both switches say when another development fact holds: tests, CI, a dev server or make, a test state folder."""
PINNED = "This installation is pinned or isn't a uv tool, so it can't update itself."
CHECKS_OFF = "Update checks are off, so hz neither looks for updates nor installs them by itself."
"""What both switches say while `update_check` is off: no automatic update without a check."""
TEST_OVERRIDE = "HEINZEL_AUTO_UPDATE_TEST"
"""Set to 1 by a test that drives the real automatic-update flow: none of the development facts below count then."""
DEVELOPMENT_VARIABLES = (
    "HZ_DEV",
    "HZ_TEST",
    "CI",
    "PYTEST_CURRENT_TEST",
    "PYTEST_XDIST_WORKER",
    "TEST_WORKER_INDEX",  # the Playwright test runner
    "PLAYWRIGHT_TEST_BASE_URL",
    "MAKELEVEL",  # started by a make target
    "npm_lifecycle_event",  # started by an npm script, such as the Vite dev server
)
"""Environment variables that mean this process runs for development, a test or CI, whatever their value."""


def _distribution_is_editable() -> bool:
    """Whether the installed `heinzel` distribution is an editable install, as its `direct_url.json` records it."""
    try:
        text = importlib.metadata.distribution("heinzel").read_text("direct_url.json")
        data: object = json.loads(text) if text else None
    except (importlib.metadata.PackageNotFoundError, ValueError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    info: object = cast("dict[str, object]", data).get("dir_info")
    return isinstance(info, dict) and cast("dict[str, object]", info).get("editable") is True


def _default_state_dir() -> Path:
    factory = Settings.model_fields["state_dir"].default_factory
    return cast("Callable[[], Path]", factory)()


def _is_checkout(folder: Path) -> bool:
    return (folder / ".git").exists() or (folder / "pyproject.toml").is_file()


def development_reason(
    state_dir: Path,
    *,
    environ: Mapping[str, str] | None = None,
    package: Path | None = None,
    prefix: Path | None = None,
    editable: Callable[[], bool] = _distribution_is_editable,
) -> str | None:
    """Why this process runs for development, judged by facts; None for an installed hz with the user's state folder.

    Automatic updates never run then: an update would replace code someone is working on, or race a test (ADR 0116).
    The facts: the code is imported from a source tree or an editable install, or its environment sits in a checkout
    (a `.venv` next to `.git`); a development, test or CI variable is set; make or npm started it; or the state folder
    isn't the user's default. `HEINZEL_AUTO_UPDATE_TEST=1` turns them all off, for the test of the real flow.
    """
    env = os.environ if environ is None else environ
    if env.get(TEST_OVERRIDE) == "1":
        return None
    package = package or Path(__file__).resolve().parents[2]  # the `heinzel` package folder
    prefix = prefix or Path(sys.prefix)
    if "site-packages" not in package.parts or editable() or _is_checkout(prefix.parent):
        return SOURCE_CHECKOUT
    if any(name in env for name in DEVELOPMENT_VARIABLES):
        return DEVELOPMENT
    try:
        default = _default_state_dir().resolve()
        if state_dir.expanduser().resolve() != default:
            return DEVELOPMENT
    except OSError:
        return DEVELOPMENT
    return None


def update_summary(plan: Plan) -> str:
    """What a plan installs, in plain words, for the log."""
    parts = [f"hz {plan.core}"] if plan.core else []
    return ", ".join([*parts, *(f"{name} {version}" for name, version in plan.plugins.items())])


def canonical_name(name: str) -> str:
    """A distribution's canonical name, without extras: `Heinzel_CRM[x]` is `heinzel-crm`."""
    return re.sub(r"[-_.]+", "-", name.split("[", 1)[0]).lower()


def old_enough(published: datetime | None, hours: int, now: datetime) -> bool:
    """Whether a release published at `published` is at least `hours` old; an unknown time counts as too new."""
    if hours <= 0:
        return True
    return published is not None and now - published >= timedelta(hours=hours)
