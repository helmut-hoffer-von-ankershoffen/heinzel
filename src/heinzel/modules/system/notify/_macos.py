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
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
SOURCES = Path(__file__).parent / "macos"
"""The app's sources: main.swift, icon.swift, its artwork (heinzel*.svg) and the Info.plist template."""
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
BUILD_SCRIPT = SOURCES.parents[5] / "scripts" / "notifier.py"
"""The build script of a source checkout (`make notifier`); a wheel has none, it ships the built app."""
BUILD_TIMEOUT = 600

Runner = Callable[..., subprocess.CompletedProcess[str]]
"""Runs a command: `runner(command)` or `runner(command, timeout)`."""
LABEL_PREFIX = f"application.{IDENTIFIER}."
"""How launchd names every instance LaunchServices starts of Heinzel Notifier: the exact bundle id, then ids."""
LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
"""LaunchServices' own tool; `-dump` lists every app it knows, `-u <path>` forgets one."""
REGISTRATION_TIMEOUT = 60
"""Seconds `lsregister -dump` may take; it prints a few hundred thousand lines."""
_BLOCK = re.compile(r"^-{20,}$", re.MULTILINE)
REAL_ENV = "HZ_REAL_NOTIFIER"
"""A test that must start the real app sets this; any other test that would is refused (see `_launch`)."""
TESTS_REFUSED = "tests don't launch the real Heinzel Notifier: each copy macOS meets stays registered under its name"
ALREADY_GONE = "No such process"
"""What `open -W` says when the app ended before it could wait for it."""
LIFETIME = 80
"""Seconds an instance may live: it asks macOS for 60 and posts; a click needs a few. The app ends itself then."""
STALE_AFTER = 5 * 60
"""An instance older than this is left over from a build that could not end itself, or a hang."""
_ETIME = re.compile(r"(?:(?:(?P<d>\d+)-)?(?P<h>\d+):)?(?P<m>\d+):(?P<s>\d+)")


def source_version(sources: Path = SOURCES) -> str:
    """The app's version: a digest of its sources, so a changed source means a new app."""
    digest = hashlib.sha256()
    for name in (
        "Info.plist",
        "heinzel-swarm.svg",
        "heinzel.svg",
        "icon.swift",
        "main.swift",
    ):  # scripts/notifier.py SOURCE_NAMES
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
class Instance:
    """One running Heinzel Notifier process, as launchd lists it by its exact bundle id."""

    pid: int
    label: str
    seconds: int | None
    """How long it has run, None when `ps` couldn't say."""

    @property
    def stale(self) -> bool:
        """Whether it has run longer than any notification needs (`STALE_AFTER`); unknown age counts as stale."""
        return self.seconds is None or self.seconds > STALE_AFTER


def parse_etime(text: str) -> int | None:
    """Seconds in `ps`'s `[[dd-]hh:]mm:ss`; None if it isn't that."""
    match = _ETIME.fullmatch(text.strip())
    if match is None:
        return None
    parts = {name: int(value or 0) for name, value in match.groupdict().items()}
    return ((parts["d"] * 24 + parts["h"]) * 60 + parts["m"]) * 60 + parts["s"]


@dataclass(frozen=True, slots=True)
class Install:
    """Where the app is installed and whether it matches the bundled one."""

    path: Path
    state: str
    """`current`, `installed` (just copied), `outdated` (differs and couldn't be replaced), `missing` (no bundle)."""
    detail: str = ""


class NotifierApp:
    """The bundled app and its installed copy; every command goes through `runner`, so tests run none."""

    def __init__(  # noqa: PLR0913 - the seams tests replace; all defaulted
        self,
        home: Path | None = None,
        *,
        bundled: Path = BUNDLED,
        runner: Runner = run,
        open_command: str = "/usr/bin/open",
        build_script: Path | None = BUILD_SCRIPT,
        kill: Callable[[int, int], None] = os.kill,
    ) -> None:
        """Install into `home`/Applications (the user's home by default) from `bundled`."""
        self.bundled = bundled
        self.path = (home or Path.home()) / "Applications" / INSTALLED_NAME
        self._runner = runner
        self._open = open_command
        self._build_script = build_script
        self._kill = kill

    @property
    def available(self) -> bool:
        """Whether there is an app to use: bundled with this installation, or installed before."""
        return tree_digest(self.bundled) is not None or tree_digest(self.path) is not None

    def rebuild(self) -> str | None:
        """In a source checkout, rebuild the bundled app when its sources changed; why it failed, else None.

        A wheel ships the app built when it was installed and has no build script: nothing to do there.
        """
        script = self._build_script
        if script is None or not script.is_file():
            return None
        if self._runner([sys.executable, str(script), "--check"]).returncode == 0:
            return None
        built = self._runner([sys.executable, str(script)], BUILD_TIMEOUT)
        if built.returncode == 0:
            return None
        lines = (built.stderr or built.stdout).strip().splitlines()
        return lines[-1] if lines else f"the build exited with {built.returncode}"

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
            return Install(
                self.path,
                state,
                "this installation carries no Heinzel Notifier: install hz again on a Mac with the Xcode Command Line "
                "Tools (xcode-select --install)",
            )
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

    def instances(self) -> list[Instance]:
        """The Heinzel Notifier processes running now, found by the bundle id in launchd's label, never by a name.

        `launchctl list` names what LaunchServices started `application.<bundle id>.<ids>`; only labels with exactly
        this app's bundle id and a live pid count, so no other process is ever matched.
        """
        listed = self._runner(["/bin/launchctl", "list"])
        if listed.returncode != 0:
            return []
        found: list[Instance] = []
        for line in listed.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) != 3 or not fields[2].startswith(LABEL_PREFIX) or not fields[0].isdecimal():  # noqa: PLR2004
                continue
            pid = int(fields[0])
            aged = self._runner(["/bin/ps", "-o", "etime=", "-p", str(pid)])
            found.append(Instance(pid, fields[2], parse_etime(aged.stdout) if aged.returncode == 0 else None))
        return found

    def registered(self) -> list[Path]:
        """Every app LaunchServices knows by this bundle id, wherever it is or was.

        macOS registers a copy the first time it is launched from a new path. A test, a build in a worktree or a
        staging folder leaves one each, and System Settings > Notifications lists them all under one name.
        """
        dumped = self._runner([LSREGISTER, "-dump"], REGISTRATION_TIMEOUT)
        if dumped.returncode != 0:
            return []
        paths: list[Path] = []
        for block in _BLOCK.split(dumped.stdout):
            path: str | None = None
            ours = False
            for line in block.splitlines():
                field, _, value = line.partition(":")
                if field == "path":
                    path = value.strip().rpartition(" (0x")[0] or None  # `<path> (0x<id>)`
                elif field == "identifier":
                    ours = value.strip() == IDENTIFIER
            if path is not None and ours:
                paths.append(Path(path))
        return paths

    def unregister_strays(self) -> int:
        """Make LaunchServices forget every copy of this app except the installed one; how many were forgotten.

        Only paths registered under this app's exact bundle id are touched, and the files are left alone: a copy in
        a worktree is a build, not ours to delete.
        """
        forgotten = 0
        for path in self.registered():
            if path == self.path:
                continue
            if self._runner([LSREGISTER, "-u", str(path)], TIMEOUT).returncode == 0:
                forgotten += 1
        return forgotten

    def forget(self) -> None:
        """Make LaunchServices forget the installed copy, for a test that installed one in a temporary home."""
        self._runner([LSREGISTER, "-u", str(self.path)], TIMEOUT)

    def clean(self, *, older_than: int = STALE_AFTER) -> int:
        """End the instances that have run longer than `older_than` seconds; how many were ended.

        Only processes launchd lists under this app's exact bundle id are touched. A notification being posted
        right now is seconds old and is left alone.
        """
        ended = 0
        for instance in self.instances():
            if instance.seconds is not None and instance.seconds <= older_than:
                continue
            try:
                self._kill(
                    instance.pid, signal.SIGKILL
                )  # the app holds nothing to save; SIGTERM could wait on what hangs
            except OSError:
                continue
            ended += 1
        return ended

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
        # A test in a temporary home would start an app from a path macOS has never seen, and macOS would list it in
        # System Settings > Notifications for good: hundreds of "Heinzel Notifier" entries. Fakes are not refused.
        if self._runner is run and os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("HZ_REAL_NOTIFIER"):
            return f"open failed: {TESTS_REFUSED}"
        with tempfile.TemporaryDirectory(prefix="hz-notifier-") as directory:
            out = Path(directory) / "out"
            out.touch(mode=0o600)
            command = [self._open, "-n", "-g", "-W", "--stdout", str(out), "--stderr", os.devnull, str(self.path)]
            result = self._runner([*command, "--args", *arguments], timeout)
            said = out.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        if result.returncode == 124 or said[-1:] == ["waiting"]:  # noqa: PLR2004 - timeout(1)'s exit code
            return "waiting: macOS is asking whether to allow Heinzel Notifier's notifications"
        # An app that ends at once can be gone before `open -W` starts watching it; it had already written its answer.
        if result.returncode != 0 and not (said and ALREADY_GONE in result.stderr):
            return f"open failed: {(result.stderr or result.stdout).strip() or f'exit {result.returncode}'}"
        return said[-1] if said else "no answer from Heinzel Notifier"
