"""A copy of the uv tool environment taken before an automatic update, to put hz and every plugin back (ADR 0116).

Reinstalling "the previous version" from a package index or a tag would change what the uv receipt pins and could
resolve plugins anew. A copy of the whole environment folder puts back exactly the bytes that ran: hz, every plugin,
their dependencies and the receipt, at the same path, so the `hz` link and the running process keep working.
"""

import contextlib
import importlib.metadata
import shutil
from dataclasses import dataclass
from pathlib import Path

PREFIX = "heinzel"
"""Distributions whose versions the copy records: hz itself and its plugins."""


def installed_versions(prefix: Path) -> dict[str, str]:
    """The installed version of hz and of every plugin in the environment at `prefix`, by distribution."""
    paths = [str(path) for path in prefix.glob("lib/python*/site-packages")]
    paths += [str(path) for path in prefix.glob("Lib/site-packages")]
    found: dict[str, str] = {}
    for distribution in importlib.metadata.distributions(path=paths):
        name = (distribution.metadata["Name"] or "").lower().replace("_", "-")
        if name.startswith(PREFIX):
            found[name] = distribution.version
    return dict(sorted(found.items()))


@dataclass(frozen=True)
class ToolSnapshot:
    """The environment at `prefix` as it was, copied to `copy`, with the versions it held."""

    prefix: Path
    copy: Path
    versions: dict[str, str]

    @classmethod
    def take(cls, prefix: Path, copy: Path) -> "ToolSnapshot":
        """Copy the environment at `prefix` to `copy`, replacing an older copy there.

        Raises:
            OSError: If it can't be copied, for example for lack of space; then nothing may be updated.
        """
        if copy.exists():
            shutil.rmtree(copy)
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(prefix, copy, symlinks=True)
        return cls(prefix, copy, installed_versions(prefix))

    def restore(self) -> None:
        """Put the copy back at `prefix`, replacing what an update installed there.

        Raises:
            OSError: If it can't be put back; the environment is then as the update left it.
        """
        broken = self.prefix.with_name(f".{self.prefix.name}.hz-failed-update")
        if broken.exists():
            shutil.rmtree(broken)
        self.prefix.rename(broken)
        try:
            shutil.copytree(self.copy, self.prefix, symlinks=True)
        except OSError:
            with contextlib.suppress(OSError):
                shutil.rmtree(self.prefix)
            broken.rename(self.prefix)
            raise
        shutil.rmtree(broken, ignore_errors=True)

    def discard(self) -> None:
        """Delete the copy."""
        shutil.rmtree(self.copy, ignore_errors=True)
