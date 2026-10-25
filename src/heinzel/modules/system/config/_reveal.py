"""Show a configuration file in this machine's file manager: Finder, Explorer, or the desktop's own on Linux.

Nothing is created: a file that doesn't exist yet is shown by its nearest existing folder. The file manager is
started detached, never waited for, with an argument list resolved by `shutil.which`; no shell.
"""

import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

Opener = Callable[[Sequence[str]], None]
"""Starts a file manager with this command line and returns without waiting for it."""

_SESSION = ("DISPLAY", "WAYLAND_DISPLAY")
"""Without one of these there is no desktop session on Linux to open a file manager in."""


def launch(command: Sequence[str]) -> None:
    """Start `command` detached from hz, its output discarded; it lives on as a window, never waited for."""
    subprocess.Popen(  # noqa: S603 - an executable resolved with shutil.which, arguments only, no shell
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


@dataclass(frozen=True, slots=True)
class Revealed:
    """What revealing a file did."""

    supported: bool
    opened: bool
    message: str


@dataclass(frozen=True, slots=True)
class Revealer:
    """This machine's file manager: how it is called and whether hz can open it here."""

    platform: str
    """`sys.platform`."""
    which: Callable[[str], str | None]
    environ: Mapping[str, str]
    opener: Opener = field(default=launch, repr=False, compare=False)

    @property
    def file_manager(self) -> str:
        """What the person knows it as: Finder, Explorer, or the file manager."""
        if self.platform == "darwin":
            return "Finder"
        if self.platform == "win32":
            return "Explorer"
        return "file manager"

    @property
    def _program(self) -> str:
        if self.platform == "darwin":
            return "open"
        return "explorer" if self.platform == "win32" else "xdg-open"

    def unsupported(self) -> str | None:
        """Why hz can't open the file manager here, or None when it can."""
        if self.platform not in {"darwin", "win32"} and not any(self.environ.get(name) for name in _SESSION):
            return "There is no desktop session (DISPLAY or WAYLAND_DISPLAY) to open a file manager in"
        if self.which(self._program) is None:
            return f"{self._program} isn't installed"
        return None

    def reveal(self, path: Path) -> Revealed:
        """Open a folder, or show a file selected, in the file manager; its nearest existing folder while it is missing.

        A file is only ever selected or its folder opened, never opened itself: that could run it.
        """
        if problem := self.unsupported():
            return Revealed(supported=False, opened=False, message=f"{problem}; the path is {path}")
        executable = self.which(self._program) or self._program
        # On macOS a folder with a suffix may be an app or a package, which `open` would launch: select it instead.
        if path.is_dir() and not (self.platform == "darwin" and path.suffix):
            return self._open([executable, str(path)], path, f"Opened {path} in {self.file_manager}.")
        folder = next((d for d in path.parents if d.is_dir()), Path(path.anchor))
        exists = path.exists()
        if self.platform == "darwin":
            command = [executable, "-R", str(path)] if exists else [executable, str(folder)]
        elif self.platform == "win32":
            command = [executable, f"/select,{path}"] if exists else [executable, str(folder)]
        else:
            command = [executable, str(path.parent if exists else folder)]
        if exists:
            message = f"Showed {path} in {self.file_manager}."
        else:
            message = f"{path} doesn't exist yet; opened {folder} in {self.file_manager}."
        return self._open(command, path, message)

    def _open(self, command: list[str], path: Path, message: str) -> Revealed:
        try:
            self.opener(command)
        except OSError as exc:
            message = f"{self._program} failed: {exc.strerror or exc}; the path is {path}"
            return Revealed(supported=True, opened=False, message=message)
        return Revealed(supported=True, opened=True, message=message)
