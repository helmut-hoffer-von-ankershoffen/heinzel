"""The Claude Code export: chosen skills as `heinzel-<name>/` directories hz marks, lists and alone replaces.

A skill in Claude Code can run its scripts, so nothing is exported without the person naming it (ADR 0061). An export
directory holds only what hz read and checked: `SKILL.md` with `name` and `description` alone (Claude Code would
honour `allowed-tools` and `model`, so no hint goes along) and the skill's listed files, copied from their checked
bytes. hz replaces or removes a directory only when it carries hz's marker *and* hz's manifest in the state directory
lists it; anything else under that name is a conflict, left alone.
"""

import json
import os
import shutil
import stat
import tempfile
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

import yaml

from heinzel.kernel import read_skill_file, skill_name_problem
from heinzel.modules.system.skill._models import ExportAction, ExportItem, ExportResult

if TYPE_CHECKING:
    from heinzel.modules.system.skill._service import Offered

PREFIX = "heinzel-"
MARKER = ".heinzel-export"
MANIFEST = "skill-exports.json"
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _is_link(path: Path) -> bool:
    try:
        return stat.S_ISLNK(path.lstat().st_mode)
    except OSError:
        return False


def exported_skill_md(offered: "Offered") -> str:
    """The `SKILL.md` of an export: the name with hz's prefix, the description, and the body; no hint."""
    head = yaml.safe_dump(
        {"name": f"{PREFIX}{offered.skill.name}", "description": offered.skill.description},
        sort_keys=False,
        allow_unicode=True,
        width=10_000,
    )
    return f"---\n{head}---\n\n{offered.skill.body}"


class Exporter:
    """Writes, refreshes and removes exports in each export directory, keeping a manifest of what it wrote."""

    def __init__(self, directories: list[Path], state_dir: Path) -> None:
        """Create the exporter for `directories`, with its manifest in `state_dir`."""
        self._directories = directories
        self._manifest_path = state_dir / MANIFEST

    # The manifest: what hz exported where, with each skill's fingerprint then.

    def _manifest(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        # Only directories exports go to now and names a skill can have: a manifest someone edited can't make hz
        # remove anything else.
        bases = {str(directory) for directory in self._directories}
        manifest: dict[str, dict[str, str]] = {}
        for base, entries in cast("dict[object, object]", data).items():
            if str(base) in bases and isinstance(entries, dict):
                pairs = cast("dict[object, object]", entries).items()
                manifest[str(base)] = {str(k): str(v) for k, v in pairs if skill_name_problem(str(k)) is None}
        return manifest

    def _save(self, manifest: Mapping[str, Mapping[str, str]]) -> None:
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
        kept = dict(self._stored()) | {base: dict(entries) for base, entries in manifest.items()}
        kept = {base: entries for base, entries in kept.items() if entries}
        handle, temporary = tempfile.mkstemp(prefix=".skill-exports-", dir=self._manifest_path.parent)
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(kept, file, indent=2, sort_keys=True)
        Path(temporary).replace(self._manifest_path)

    def _stored(self) -> dict[str, dict[str, str]]:
        """Entries for directories no longer configured, kept as they are so a later configuration finds them."""
        try:
            data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        bases = {str(directory) for directory in self._directories}
        return {
            str(base): {str(k): str(v) for k, v in cast("dict[object, object]", entries).items()}
            for base, entries in cast("dict[object, object]", data).items()
            if str(base) not in bases and isinstance(entries, dict)
        }

    def exported(self) -> dict[str, list[Path]]:
        """Each exported skill's name with the directories it is exported to."""
        found: dict[str, list[Path]] = {}
        for base, entries in self._manifest().items():
            for name in entries:
                found.setdefault(name, []).append(Path(base) / f"{PREFIX}{name}")
        return found

    # Writing.

    def export(self, skills: list["Offered"]) -> ExportResult:
        """Write each skill into every export directory, replacing only exports hz made before."""
        manifest = self._manifest()
        items: list[ExportItem] = []
        for base in self._directories:
            entries = manifest.setdefault(str(base), {})
            if _is_link(base):
                items += [
                    ExportItem(
                        name=o.skill.name,
                        action="conflict",
                        target=base,
                        detail=f"{base} is a symbolic link; hz writes only into a real directory",
                    )
                    for o in skills
                ]
                continue
            try:
                base.mkdir(parents=True, exist_ok=True, mode=0o700)
            except OSError as exc:
                detail = f"{base} can't be created ({exc.strerror or type(exc).__name__})"
                items += [ExportItem(name=o.skill.name, action="conflict", target=base, detail=detail) for o in skills]
                continue
            for offered in skills:
                item = self._write(base, offered, known=offered.skill.name in entries)
                if item.action == "exported":
                    entries[offered.skill.name] = offered.skill.fingerprint
                items.append(item)
        self._save(manifest)
        return ExportResult(
            items=items,
            next=["Restart Claude Code or start a new session to pick up the skills.", "hz system skill list"],
        )

    def _write(self, base: Path, offered: "Offered", *, known: bool) -> ExportItem:
        name = offered.skill.name
        target = base / f"{PREFIX}{name}"
        if problem := self._not_ours(target, known=known):
            return ExportItem(name=name, action="conflict", target=target, detail=problem)
        staging = Path(tempfile.mkdtemp(prefix=f".{PREFIX}", dir=base))  # mode 0700
        try:
            _write_file(staging / "SKILL.md", exported_skill_md(offered).encode("utf-8"))
            for file in offered.skill.files:
                content = read_skill_file(offered.directory, offered.skill, file.path)
                destination = staging.joinpath(*file.path.split("/"))
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                _write_file(destination, content)
            marker = {"name": name, "fingerprint": offered.skill.fingerprint, "module": offered.module}
            _write_file(staging / MARKER, json.dumps(marker).encode("utf-8"))
            if target.exists():
                retired = Path(tempfile.mkdtemp(prefix=f".{PREFIX}old-", dir=base))
                target.rename(retired / "skill")
                staging.rename(target)
                shutil.rmtree(retired)
            else:
                staging.rename(target)
        except (OSError, ValueError) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            return ExportItem(name=name, action="conflict", target=target, detail=f"not written: {exc}")
        return ExportItem(name=name, action="exported", target=target)

    @staticmethod
    def _not_ours(target: Path, *, known: bool) -> str | None:
        """Why hz must leave `target` alone, or None when it is free or an export hz made and lists."""
        if _is_link(target):
            return f"{target} is a symbolic link; hz leaves it alone"
        if not target.exists():
            return None
        if not target.is_dir() or not (target / MARKER).is_file() or _is_link(target / MARKER):
            return f"{target} exists and isn't an export of hz's; move it away to export this skill"
        if not known:
            return f"{target} isn't in hz's list of exports; remove it yourself to export this skill again"
        return None

    # Removing.

    def remove(self, names: Collection[str] | None) -> ExportResult:
        """Remove the exports of `names` (all of hz's when None) from every directory hz exported to."""
        manifest = self._manifest()
        items: list[ExportItem] = []
        for base, entries in manifest.items():
            for name in [n for n in entries if names is None or n in names]:
                items.append(self._delete(Path(base), name, "removed", ""))
                if items[-1].action != "conflict":
                    del entries[name]
        self._save(manifest)
        return ExportResult(items=items, next=["hz system skill list"])

    def sync(self, skills: Mapping[str, "Offered"]) -> ExportResult:
        """Remove exports whose skill is gone or changed since it was exported; never add one.

        A changed skill could now run something its export didn't, so the person exports it again to take it.
        """
        manifest = self._manifest()
        items: list[ExportItem] = []
        for base, entries in manifest.items():
            for name, exported in list(entries.items()):
                offered = skills.get(name)
                if offered is not None and offered.skill.fingerprint == exported:
                    continue
                action: ExportAction
                action, detail = (
                    ("removed", "its skill is no longer installed")
                    if offered is None
                    else ("changed", f"its skill changed; review it and run `hz system skill export --name {name}`")
                )
                items.append(self._delete(Path(base), name, action, detail))
                if items[-1].action != "conflict":
                    del entries[name]
        self._save(manifest)
        return ExportResult(items=items, next=[])

    @staticmethod
    def _delete(base: Path, name: str, action: ExportAction, detail: str) -> ExportItem:
        target = base / f"{PREFIX}{name}"
        marker = target / MARKER
        if _is_link(target) or (target.exists() and (_is_link(marker) or not marker.is_file())):
            return ExportItem(
                name=name, action="conflict", target=target, detail=f"{target} isn't hz's export; left alone"
            )
        if target.exists():
            shutil.rmtree(target)
        return ExportItem(name=name, action=action, target=target, detail=detail)


def _write_file(path: Path, content: bytes) -> None:
    """Write a new private file (0600), refusing one that exists or a link in its place."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
