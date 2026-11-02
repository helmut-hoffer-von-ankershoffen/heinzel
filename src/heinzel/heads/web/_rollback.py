"""A copy of the uv tool environment taken before an automatic update, to put hz and every plugin back (ADR 0116).

Reinstalling "the previous version" from a package index or a tag would change what the uv receipt pins and could
resolve plugins anew. A copy of the whole environment folder puts back exactly the bytes that ran: hz, every plugin,
their dependencies and the receipt, at the same path, so the `hz` link and the running process keep working.
"""

import contextlib
import importlib.metadata
import json
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

CORE = "heinzel"


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def installed_versions(prefix: Path, names: Iterable[str]) -> dict[str, str]:
    """The installed version of each distribution in `names` (hz and its plugins) at `prefix`; absent ones left out."""
    wanted = {_canonical(name) for name in names}
    paths = [str(path) for path in prefix.glob("lib/python*/site-packages")]
    paths += [str(path) for path in prefix.glob("Lib/site-packages")]
    found: dict[str, str] = {}
    for distribution in importlib.metadata.distributions(path=paths):
        name = _canonical(distribution.metadata["Name"] or "")
        if name in wanted:
            found[name] = distribution.version
    return dict(sorted(found.items()))


def installed_commit(prefix: Path) -> str | None:
    """The commit hz was installed from, as its `direct_url.json` records it; None for an index or a wheel."""
    paths = [str(path) for path in prefix.glob("lib/python*/site-packages")]
    for distribution in importlib.metadata.distributions(path=paths, name=CORE):
        try:
            text = distribution.read_text("direct_url.json")
            data: object = json.loads(text) if text else None
        except (OSError, ValueError):
            return None
        vcs: object = cast("dict[str, object]", data).get("vcs_info") if isinstance(data, dict) else None
        commit: object = cast("dict[str, object]", vcs).get("commit_id") if isinstance(vcs, dict) else None
        return commit if isinstance(commit, str) else None
    return None


@dataclass(frozen=True)
class ToolSnapshot:
    """The environment at `prefix` as it was, copied to `copy`, with the versions it held."""

    prefix: Path
    copy: Path
    versions: dict[str, str]
    """hz and every plugin as they were, by distribution."""

    @classmethod
    def take(cls, prefix: Path, copy: Path, plugins: Iterable[str]) -> "ToolSnapshot":
        """Copy the environment at `prefix` to `copy`, replacing an older copy there; record hz and `plugins`.

        Raises:
            OSError: If it can't be copied, for example for lack of space; then nothing may be updated.
        """
        if copy.exists():
            shutil.rmtree(copy)
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(prefix, copy, symlinks=True)
        return cls(prefix, copy, installed_versions(prefix, [CORE, *plugins]))

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

    def installed_now(self, names: Iterable[str]) -> dict[str, str]:
        """What the environment holds now, after an update, of the distributions `names`."""
        return installed_versions(self.prefix, names)

    def commit_now(self) -> str | None:
        """The commit hz is installed from now; None when it isn't from git."""
        return installed_commit(self.prefix)

    def discard(self) -> None:
        """Delete the copy."""
        shutil.rmtree(self.copy, ignore_errors=True)
