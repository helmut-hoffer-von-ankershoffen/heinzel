"""Heinzel Notifier, hz's own macOS notifier app (ADR 0043): bundled in the wheel, installed into ~/Applications.

The release build compiles `macos/main.swift` into a universal, ad-hoc signed `HeinzelNotifier.app` (`make
notifier`) that ships inside the package; nothing is compiled on a person's Mac. hz copies it to `~/Applications/Heinzel
Notifier.app` when it is missing or differs from the bundled one, and checks the copy against the bundle's digest.
Installed by Python from a wheel, it carries no quarantine flag, so Gatekeeper doesn't stop it.

hz starts it with `open -n`, through LaunchServices, which puts it in the person's GUI session: that is what lets the
daemon (launchd) and SSH sessions post notifications. Run directly, or as the child of a sandboxed process, macOS
refuses ("Notifications are not allowed for this application"). The first notification shows macOS's question, with
the Heinzel icon, whether to allow them; the person answers once.
"""

import hashlib
import logging
import os
import plistlib
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
SOURCES = Path(__file__).parent / "macos"
"""The app's sources: main.swift, icon.swift and the Info.plist template."""
BUNDLED = SOURCES / "HeinzelNotifier.app"
"""Where `make notifier` puts the built app, and where the wheel ships it."""
INSTALLED_NAME = "Heinzel Notifier.app"
EXECUTABLE = Path("Contents") / "MacOS" / "notifier"
IDENTIFIER = "dev.heinzel.notifier"
TIMEOUT = 10
"""Seconds `open -W` may wait for the app; longer means macOS is asking the person whether to allow it."""
ASKING_TIMEOUT = 90
"""Seconds `install` waits while macOS asks the person; the app itself gives up after 60."""
QUARANTINE = "com.apple.quarantine"

Runner = Callable[..., subprocess.CompletedProcess[str]]
"""Runs a command: `runner(command)` or `runner(command, timeout)`."""


def source_version(sources: Path = SOURCES) -> str:
    """The app's version: a digest of its sources, so a changed source means a new app."""
    digest = hashlib.sha256()
    for name in ("Info.plist", "icon.swift", "main.swift"):
        digest.update(name.encode() + b"\0" + (sources / name).read_bytes() + b"\0")
    return digest.hexdigest()[:16]


def tree_digest(app: Path) -> str | None:
    """SHA-256 over every file of an app bundle, by relative path; None if it isn't one."""
    if not (app / EXECUTABLE).is_file():
        return None
    digest = hashlib.sha256()
    for path in sorted(p for p in app.rglob("*") if p.is_symlink() or p.is_file()):
        content = b"link:" + str(path.readlink()).encode() if path.is_symlink() else path.read_bytes()
        digest.update(path.relative_to(app).as_posix().encode() + b"\0" + content + b"\0")
    return digest.hexdigest()


def bundle_version(app: Path) -> str | None:
    """`CFBundleVersion` of an app bundle; None if it has none."""
    try:
        with (app / "Contents" / "Info.plist").open("rb") as handle:
            version = plistlib.load(handle).get("CFBundleVersion")
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    return version if isinstance(version, str) else None


def run(command: Sequence[str], timeout: float = TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Run a command with a timeout; a timeout becomes exit code 124, like timeout(1)."""
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)  # noqa: S603 - fixed executables, arguments only, no shell
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", f"{command[0]} timed out after {timeout:g}s")


@dataclass(frozen=True, slots=True)
class Install:
    """Where the app is installed and whether it matches the bundled one."""

    path: Path
    state: str
    """`current`, `installed` (just copied), `outdated` (differs and couldn't be replaced), `missing` (no bundle)."""
    detail: str = ""


class NotifierApp:
    """The bundled app and its installed copy; every command goes through `runner`, so tests run none."""

    def __init__(
        self,
        home: Path | None = None,
        *,
        bundled: Path = BUNDLED,
        runner: Runner = run,
        open_command: str = "/usr/bin/open",
    ) -> None:
        """Install into `home`/Applications (the user's home by default) from `bundled`."""
        self.bundled = bundled
        self.path = (home or Path.home()) / "Applications" / INSTALLED_NAME
        self._runner = runner
        self._open = open_command

    @property
    def available(self) -> bool:
        """Whether there is an app to use: bundled with this installation, or installed before."""
        return tree_digest(self.bundled) is not None or tree_digest(self.path) is not None

    def inspect(self) -> Install:
        """Whether the installed app matches the bundled one, without changing anything."""
        wanted, have = tree_digest(self.bundled), tree_digest(self.path)
        if have is None:
            return Install(self.path, "missing", "not in ~/Applications yet; the first notification installs it")
        if wanted is not None and have != wanted:
            return Install(self.path, "outdated", "it differs from the app this installation bundles")
        return Install(self.path, "current")

    def ensure(self) -> Install:
        """Copy the bundled app into ~/Applications if it is missing or differs, and verify the copy."""
        wanted = tree_digest(self.bundled)
        have = tree_digest(self.path)
        if wanted is None:
            state = "outdated" if have is not None else "missing"
            return Install(self.path, state, "this installation carries no Heinzel Notifier (built on macOS only)")
        if have == wanted:
            return Install(self.path, "current")
        try:
            self._copy()
        except OSError as exc:
            return Install(self.path, "outdated" if have else "missing", f"can't install it: {exc.strerror or exc}")
        if tree_digest(self.path) != wanted:
            return Install(self.path, "outdated", "the installed copy doesn't match the bundled app")
        return Install(self.path, "installed")

    def _copy(self) -> None:
        """Replace the installed app at once: copy beside it, then swap, so a failed copy leaves the old one.

        Raises:
            OSError: If it can't be copied or moved into place; the old app, if any, is put back.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".heinzel-notifier-", dir=self.path.parent))
        try:
            fresh = staging / INSTALLED_NAME
            shutil.copytree(self.bundled, fresh, symlinks=True)
            (fresh / EXECUTABLE).chmod(0o755)  # a wheel may not keep the executable bit
            old = staging / "old.app"
            if self.path.exists():
                self.path.rename(old)
            try:
                fresh.rename(self.path)
            except OSError:
                if old.exists():
                    old.rename(self.path)  # never leave no app at all
                raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def quarantined(self) -> bool:
        """Whether the installed app carries the quarantine flag, which makes Gatekeeper stop it."""
        result = self._runner(["/usr/bin/xattr", "-p", QUARANTINE, str(self.path)])
        return result.returncode == 0

    def status(self) -> str:
        """MacOS's answer for the app: `authorized`, `denied`, `not-determined`, `provisional`, or why unknown."""
        return self._launch(["--status"])

    def post(self, title: str, message: str, url: str | None, *, asking: bool = False) -> str:
        """Post one notification through LaunchServices; `posted`, `denied`, or what went wrong.

        With `asking`, a person is there to answer macOS's question, so it waits longer for the app.
        """
        arguments = ["--title", title, "--message", message]
        if url is not None:
            arguments += ["--open", url]
        return self._launch(arguments, ASKING_TIMEOUT if asking else TIMEOUT)

    def _launch(self, arguments: list[str], timeout: float = TIMEOUT) -> str:
        with tempfile.TemporaryDirectory(prefix="hz-notifier-") as directory:
            out = Path(directory) / "out"
            out.touch(mode=0o600)
            command = [self._open, "-n", "-g", "-W", "--stdout", str(out), "--stderr", os.devnull, str(self.path)]
            result = self._runner([*command, "--args", *arguments], timeout)
            said = out.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        if result.returncode == 124 or said[-1:] == ["waiting"]:  # noqa: PLR2004 - timeout(1)'s exit code
            return "waiting: macOS is asking whether to allow Heinzel Notifier's notifications"
        if result.returncode != 0:
            return f"open failed: {(result.stderr or result.stdout).strip() or f'exit {result.returncode}'}"
        return said[-1] if said else "no answer from Heinzel Notifier"
