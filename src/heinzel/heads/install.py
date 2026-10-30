"""Install a verified standard app composition while keeping the core wheel plugin-free (ADR 0094)."""

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from typing import cast
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import urlopen

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from heinzel import Config, HeinzelError, Progress, Settings
from heinzel.heads._tool_install import resolve_target, run_tool, target_installation, verify_fresh_python_policy
from heinzel.kernel import DaemonInstallationLifecycle, Installation, boot, subprocess_environment

PROFILE_ROOT = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download"
PROFILE_REFS = (
    "https://api.github.com/repos/helmut-hoffer-von-ankershoffen/heinzel-plugins/git/matching-refs/tags/app-v"
)
PROFILE_RELEASES = "https://api.github.com/repos/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/tags"
MAX_PROFILE = 1024 * 1024
MAX_WHEEL = 128 * 1024 * 1024
TIMEOUT = 60
PROFILE_FORMAT = 2
"""Standard apps with this lifecycle can maintain startup continuity across package changes."""


class Artifact(BaseModel):
    """One immutable built distribution; composition never selects executable build instructions."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^heinzel(?:-[a-z0-9]+(?:-[a-z0-9]+)*)?$")
    version: str = Field(max_length=80)
    filename: str = Field(pattern=r"^[A-Za-z0-9_.+-]+\.whl$", max_length=240)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    url: str = Field(max_length=4096)


class AppProfile(BaseModel):
    """An installer-approved composition, independent of plugin repository discovery."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    format: int = Field(strict=True, ge=1, le=PROFILE_FORMAT)
    core_version: str
    sdk: int = Field(ge=1, le=1)
    artifacts: list[Artifact] = Field(min_length=1, max_length=64)


def _read(source: str, limit: int) -> bytes:
    parsed = urlsplit(source)
    if parsed.scheme:
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
            msg = "App artifacts require HTTPS without credentials or fragments"
            raise ValueError(msg)
        with urlopen(source, timeout=TIMEOUT) as response:  # noqa: S310 - checked HTTPS, no credential headers
            if urlsplit(cast("str", response.url)).scheme != "https":
                msg = "App artifact redirected away from HTTPS"
                raise ValueError(msg)
            content = cast("bytes", response.read(limit + 1))
    else:
        with Path(source).open("rb") as handle:
            content = handle.read(limit + 1)
    if len(content) > limit:
        msg = "App artifact exceeds its size limit"
        raise ValueError(msg)
    return content


def read_profile(source: str) -> AppProfile:
    """Read a bounded composition and verify every wheel identity before installing anything.

    Raises:
        ValueError: If the profile has duplicate identities or mismatched wheel metadata.
        OSError: If the profile cannot be read.
    """
    profile = AppProfile.model_validate_json(_read(source, MAX_PROFILE))
    names: set[str] = set()
    for artifact in profile.artifacts:
        name, version, _, _ = parse_wheel_filename(artifact.filename)
        if name != canonicalize_name(artifact.name) or str(version) != artifact.version or name in names:
            msg = "App profile has a duplicate or mismatched wheel identity"
            raise ValueError(msg)
        if urlsplit(source).scheme and urlsplit(artifact.url).scheme != "https":
            msg = "A remote app profile cannot select a local file"
            raise ValueError(msg)
        names.add(name)
    core = [artifact for artifact in profile.artifacts if artifact.name == "heinzel"]
    if len(core) != 1 or core[0].version != profile.core_version:
        msg = "App profile must contain exactly its declared core version"
        raise ValueError(msg)
    return profile


def _stable_release(version: Version) -> bool:
    """Require a published stable release as well as a stable-looking tag.

    Raises:
        ValueError: If GitHub's release metadata is malformed.
    """
    value: object = json.loads(_read(f"{PROFILE_RELEASES}/app-v{version}", MAX_PROFILE))
    release = cast("dict[str, object]", value) if isinstance(value, dict) else {}
    if not all(isinstance(release.get(key), bool) for key in ("draft", "prerelease")):
        msg = "The standard app release metadata is invalid"
        raise ValueError(msg)
    return release["draft"] is False and release["prerelease"] is False


def _profile_versions() -> list[Version]:
    """Read the bounded stable app-tag index, newest first.

    Raises:
        TypeError: If the release index is not an array.
    """
    refs: object = json.loads(_read(PROFILE_REFS, MAX_PROFILE))
    if not isinstance(refs, list):
        msg = "The standard app release index is invalid"
        raise TypeError(msg)
    versions: set[Version] = set()
    for item in cast("list[object]", refs):
        ref = cast("dict[str, object]", item).get("ref", "") if isinstance(item, dict) else ""
        if not isinstance(ref, str) or not ref.startswith("refs/tags/app-v"):
            continue
        raw = ref.removeprefix("refs/tags/app-v")
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        if str(version) == raw and not version.is_prerelease and not version.is_devrelease:
            versions.add(version)
    return sorted(versions, reverse=True)[:32]


def latest_profile() -> AppProfile:
    """Select the newest published app independently of the bootstrap SDK's version.

    Raises:
        ValueError: If the public release index is invalid.
        HeinzelError: If no compatible composition is published yet.
        OSError: If GitHub cannot be reached or refuses the request.
        HTTPError: If GitHub refuses a request for a published profile.
        TypeError: If the release index is not an array.
    """
    # A tag can exist while its independently tested composition is still being published.
    for version in _profile_versions():
        try:
            if not _stable_release(version):
                continue
            profile = read_profile(f"{PROFILE_ROOT}/app-v{version}/standard.json")
        except HTTPError as error:
            error.close()
            if error.code == HTTPStatus.NOT_FOUND:
                continue
            raise
        if profile.core_version != str(version):
            msg = "The standard app profile does not match its release"
            raise ValueError(msg)
        if profile.format != PROFILE_FORMAT:
            continue  # Recognized legacy apps cannot preserve this launcher's verified update lifecycle.
        return profile
    msg = "No compatible standard app release is available yet; try again shortly"
    raise HeinzelError(msg)


def excluded_plugins(history: Path) -> set[str]:
    """Explicit removals win over default inclusion; a subsequent explicit add clears that exclusion."""
    excluded: set[str] = set()
    if not history.is_file():
        return excluded
    for line in history.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
            name = canonicalize_name(event.get("distribution") or "")
            if not name:
                continue
            if event.get("action") == "remove":
                excluded.add(name)
            elif event.get("action") in {"add", "develop"}:
                excluded.discard(name)
        except (ValueError, TypeError, AttributeError):
            continue
    return excluded


def prepare(profile: AppProfile, directory: Path, exclusions: set[str]) -> list[Path]:
    """Fetch and verify every selected wheel before starting the single installation command.

    Raises:
        ValueError: If any selected artifact fails its digest.
        OSError: If an artifact cannot be read or saved.
    """
    paths: list[Path] = []
    for artifact in sorted(profile.artifacts, key=lambda item: item.name != "heinzel"):
        if artifact.name in exclusions and artifact.name != "heinzel":
            continue
        content = _read(artifact.url, MAX_WHEEL)
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            msg = f"Artifact digest does not match for {artifact.name}; nothing was installed"
            raise ValueError(msg)
        target = _publish_artifact(directory, artifact, content)
        paths.append(target)
    return paths


def _artifact_directory(path: Path) -> int:
    """Open or create a private directory without following an untrusted path component.

    Raises:
        OSError: If an ancestor is unsafe or cannot be opened.
    """
    absolute = path.absolute()
    fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in absolute.parts[1:]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, 0o700, dir_fd=fd)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            _check_artifact_directory(child)
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    else:
        return fd


def _check_artifact_directory(fd: int) -> None:
    """Reject a directory writable by another user, and close the descriptor on rejection.

    Raises:
        OSError: If the directory is unsafe.
    """
    info = os.fstat(fd)
    sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
    if info.st_uid not in {0, os.getuid()} or (info.st_mode & 0o022 and not sticky_root):
        os.close(fd)
        msg = "The application artifact directory has unsafe ownership or permissions."
        raise OSError(msg)


def _publish_artifact(directory: Path, artifact: Artifact, content: bytes) -> Path:
    """Publish one verified wheel exclusively; never replace an existing filesystem entry.

    Raises:
        OSError: If the destination is unsafe or the wheel cannot be saved.
    """
    folder = directory / artifact.sha256
    parent = _artifact_directory(folder)
    try:
        try:
            fd = os.open(artifact.filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        except FileExistsError:
            fd = os.open(artifact.filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                existing = stream.read(MAX_WHEEL + 1)
                after = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o022
                or len(existing) > MAX_WHEEL
                or hashlib.sha256(existing).hexdigest() != artifact.sha256
                or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            ):
                msg = "The existing app artifact is unsafe or does not match; inspect it before retrying."
                raise OSError(msg) from None
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(parent)
    finally:
        os.close(parent)
    return folder / artifact.filename


def uv_executable() -> str:
    """Find the uv executable used by both the installer and launcher.

    Raises:
        ValueError: If uv is not installed on PATH.
    """
    executable = shutil.which("uv")
    if executable is None:
        msg = "Install uv before installing the standard app"
        raise ValueError(msg)
    return executable


def existing_requirements(prefix: Path | None = None) -> list[str]:
    """Preserve independently added plugins when refreshing the standard app composition."""
    if prefix is None:
        result = subprocess.run(  # noqa: S603 - fixed uv tool query, no shell
            [uv_executable(), "tool", "dir"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
            env=subprocess_environment(),
        )
        if result.returncode:
            return []
        prefix = Path(result.stdout.strip()) / "heinzel"
    requirements = Installation(Progress(), prefix=prefix).requirements()
    return requirements[1:] if requirements else []


def _managed_install(
    profile: AppProfile, selected: list[Artifact], paths: list[Path], settings: Settings, exclusions: set[str]
) -> int:
    """Keep the selected installation and quiescence fence through its verified checkpoint.

    Raises:
        HeinzelError: If the manager, selected artifacts or installation cannot be verified.
    """
    uv = uv_executable()
    target = resolve_target(uv)
    if not os.path.lexists(target.prefix):
        verify_fresh_python_policy(subprocess_environment())
    installation = target_installation(target, uv)
    kernel = boot(settings.model_copy(update={"plugins_enabled": False}))
    kernel.bind(Installation, installation)
    if not kernel.provides(DaemonInstallationLifecycle):
        msg = "The daemon lifecycle boundary is unavailable; the application was not installed."
        raise HeinzelError(msg)
    lifecycle = kernel.resolve(DaemonInstallationLifecycle)
    with lifecycle.quiescent(target.binary):
        by_name = {artifact.filename: artifact for artifact in selected}
        wheels = {by_name[path.name].name: path for path in paths}
        selected_names = {item.name for item in profile.artifacts}
        preserved = [
            requirement
            for requirement in existing_requirements(target.prefix)
            if canonicalize_name(Requirement(requirement).name) not in selected_names | exclusions
        ]
        expected = {item.name: (item.version, item.sha256) for item in selected}
        with _verified_overrides(settings.data_dir / "app-artifacts", wheels, expected) as overrides:
            command = [uv, "tool", "install", "heinzel", "--override", str(overrides)]
            for requirement in preserved:
                command += ["--with", requirement]
            for name in wheels:
                if name != "heinzel":
                    command += ["--with", name]
            generation = run_tool(command, target, expected, wheels, installation, preserved)
        _write_standard_receipt(settings.data_dir, profile.model_copy(update={"artifacts": selected}))
        installation.record_startup_state(expected=generation)
    return 0


@contextmanager
def _verified_overrides(
    directory: Path, wheels: Mapping[str, Path], expected: Mapping[str, tuple[str, str]]
) -> Generator[Path, None, None]:
    """Create and remove only this operation's private uv override file.

    Yields:
        The exclusive file containing independently selected wheel identities.

    Raises:
        HeinzelError: If the file was replaced before cleanup.
        OSError: If the private file cannot be written or removed.
    """
    fd, name = tempfile.mkstemp(prefix=".verified-overrides-", dir=directory)
    path = Path(name)
    info = os.fstat(fd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(
                "".join(f"{name} @ {wheel.as_uri()}#sha256={expected[name][1]}\n" for name, wheel in wheels.items())
            )
            stream.flush()
            os.fsync(stream.fileno())
        yield path
    finally:
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            msg = "The temporary verified override file changed; inspect the installation."
            raise HeinzelError(msg)
        path.unlink()


def _write_standard_receipt(directory: Path, profile: AppProfile) -> None:
    """Durably publish the selected profile without following an existing receipt symlink.

    Raises:
        OSError: If the receipt cannot be published and synchronized.
    """
    fd, name = tempfile.mkstemp(prefix=".standard-app-", dir=directory)
    partial = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(profile.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        Path(partial).replace(directory / "standard-app.json")
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        partial.unlink(missing_ok=True)


def _selected_profile(source: str | None) -> AppProfile:
    """Resolve an explicit or latest composition that supports this installer's managed lifecycle.

    Raises:
        HeinzelError: If an explicit profile uses the legacy lifecycle.
        ValueError: If the profile is invalid or no compatible release exists.
        OSError: If release metadata cannot be read.
    """
    profile = read_profile(source) if source else latest_profile()
    if profile.format != PROFILE_FORMAT:
        msg = "This app profile uses the legacy installation lifecycle. Choose a current compatible standard app."
        raise HeinzelError(msg)
    return profile


def main(argv: list[str] | None = None) -> int:
    """One standard installation command; a minimal core install remains an ordinary uv/pip install."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("HZ_APP_PROFILE"), help="Use an explicit app profile")
    parser.add_argument("--python", help="Install into this existing Python environment instead of a uv tool")
    parser.add_argument("--dry-run", action="store_true", help="Show the selected immutable composition")
    parser.add_argument("--history", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        profile = _selected_profile(args.profile)
        # History is data only. Disabled modules remain disabled in the untouched configuration file.
        settings = Config.load().settings(Settings, "hz")
        history = args.history or settings.data_dir / "plugins.jsonl"
        exclusions = excluded_plugins(history)
        selected = [item for item in profile.artifacts if item.name == "heinzel" or item.name not in exclusions]
        (sys.stdout if args.dry_run else sys.stderr).write(
            f"Heinzelmännchen {profile.core_version}: "
            + ", ".join(f"{item.name} {item.version}" for item in selected)
            + "\n"
        )
        if args.dry_run:
            return 0
        # Keep verified wheels as durable data; uv receipts retain index pins for ordinary updates.
        core, *plugins = prepare(profile, settings.data_dir / "app-artifacts", exclusions)
        if args.python:
            command = [
                uv_executable(),
                "pip",
                "install",
                "--python",
                args.python,
                str(core),
                *(str(path) for path in plugins),
            ]
            status = subprocess.run(  # noqa: S603 - verified wheel paths and an explicitly selected Python
                command, check=False, timeout=600, stdout=sys.stderr, env=subprocess_environment()
            ).returncode
        else:
            status = _managed_install(profile, selected, [core, *plugins], settings, exclusions)
    except HeinzelError as error:
        sys.stderr.write(f"Installation did not complete: {error}\n")
        return 1
    except (OSError, ValueError, TypeError, ValidationError, subprocess.SubprocessError):
        sys.stderr.write(
            "The app profile or one of its artifacts could not be verified; installation did not complete.\n"
        )
        return 1
    else:
        return status


if __name__ == "__main__":
    sys.exit(main())
