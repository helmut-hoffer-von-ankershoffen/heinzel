"""Install a verified standard app composition while keeping the core wheel plugin-free (ADR 0094)."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
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
from heinzel.kernel import Installation

PROFILE_ROOT = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download"
PROFILE_REFS = (
    "https://api.github.com/repos/helmut-hoffer-von-ankershoffen/heinzel-plugins/git/matching-refs/tags/app-v"
)
PROFILE_RELEASES = "https://api.github.com/repos/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/tags"
MAX_PROFILE = 1024 * 1024
MAX_WHEEL = 128 * 1024 * 1024
TIMEOUT = 60


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
    format: int = Field(ge=1, le=1)
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
        ValueError: If the public release index is invalid or no complete composition exists.
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
        return profile
    msg = "No complete standard app release is available; try again shortly"
    raise ValueError(msg)


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
        target = directory / artifact.sha256 / artifact.filename
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(content)
        target.chmod(0o600)
        paths.append(target)
    return paths


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


def existing_requirements() -> list[str]:
    """Preserve independently added plugins when refreshing the standard app composition."""
    result = subprocess.run([uv_executable(), "tool", "dir"], capture_output=True, text=True, check=False, timeout=15)  # noqa: S603 - fixed uv tool query, no shell
    if result.returncode:
        return []
    prefix = Path(result.stdout.strip()) / "heinzel"
    requirements = Installation(Progress(), prefix=prefix).requirements()
    return requirements[1:] if requirements else []


def main(argv: list[str] | None = None) -> int:
    """One standard installation command; a minimal core install remains an ordinary uv/pip install."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("HZ_APP_PROFILE"), help="Use an explicit app profile")
    parser.add_argument("--python", help="Install into this existing Python environment instead of a uv tool")
    parser.add_argument("--dry-run", action="store_true", help="Show the selected immutable composition")
    parser.add_argument("--history", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        profile = read_profile(args.profile) if args.profile else latest_profile()
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
        else:
            stamp = hashlib.sha256(profile.model_dump_json().encode()).hexdigest()
            overrides = settings.data_dir / "app-artifacts" / f"verified-{stamp}.txt"
            by_name = {artifact.filename: artifact for artifact in selected}
            paths = [core, *plugins]
            overrides.write_text("".join(f"{by_name[path.name].name} @ {path.as_uri()}\n" for path in paths))
            overrides.chmod(0o600)
            command = [
                uv_executable(),
                "tool",
                "install",
                "--force",
                "heinzel",
                "--override",
                str(overrides),
            ]
            selected_names = {item.name for item in profile.artifacts}
            for requirement in existing_requirements():
                name = canonicalize_name(Requirement(requirement).name)
                if name not in selected_names and name not in exclusions:
                    command += ["--with", requirement]
            for plugin in plugins:
                artifact = by_name[plugin.name]
                command += ["--with", artifact.name]
        result = subprocess.run(command, check=False, timeout=600, stdout=sys.stderr)  # noqa: S603 - verified local wheel paths, no shell
        if result.returncode == 0 and not args.python:
            receipt = settings.data_dir / "standard-app.json"
            receipt.write_text(profile.model_copy(update={"artifacts": selected}).model_dump_json(), encoding="utf-8")
            receipt.chmod(0o600)
    except (OSError, ValueError, TypeError, HeinzelError, ValidationError, subprocess.SubprocessError):
        sys.stderr.write(
            "The app profile or one of its artifacts could not be verified; installation did not complete.\n"
        )
        return 1
    else:
        return result.returncode


if __name__ == "__main__":
    sys.exit(main())
