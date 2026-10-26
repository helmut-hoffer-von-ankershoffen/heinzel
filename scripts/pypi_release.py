# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Verify a genuine core release and its complete distributions before an isolated PyPI upload."""

import hashlib
import json
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "helmut-hoffer-von-ankershoffen/heinzel"
TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
ARTIFACT_COUNT = 2
COMMIT = re.compile(r"[0-9a-f]{40}")
NOTIFIER = "heinzel/modules/system/notify/macos/HeinzelNotifier.app/Contents/MacOS/notifier"
REQUIRED = ("heinzel/__init__.py", "heinzel/CHANGELOG.md", "heinzel/heads/web/static/index.html", NOTIFIER)


class ReleaseError(ValueError):
    """The source or distributions are not a verifiable core release."""


def version_for(tag: str) -> str:
    """Only final semantic version tags are registry release candidates.

    Raises:
        ReleaseError: If the tag is not a final version tag.
    """
    if not TAG.fullmatch(tag):
        msg = "Use a genuine final release tag, vX.Y.Z; branch, prerelease and arbitrary ref uploads are refused."
        raise ReleaseError(msg)
    return tag[1:]


def _git(root: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        msg = "git is required to verify the release source."
        raise ReleaseError(msg)
    try:
        return subprocess.run(  # noqa: S603 - fixed git commands; tag is strictly validated
            [executable, *arguments], cwd=root, check=True, capture_output=True, text=True, timeout=15
        ).stdout.strip()
    except subprocess.CalledProcessError as error:
        msg = "The exact tag, checked-out commit and origin/main ancestry could not be verified."
        raise ReleaseError(msg) from error


def verify_source(root: Path, tag: str, release: dict[str, object]) -> str:
    """Bind the checkout, protected-main ancestry, version and published GitHub release.

    Raises:
        ReleaseError: If any source identity disagrees.
    """
    version = version_for(tag)
    commit = _git(root, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    if not COMMIT.fullmatch(commit) or _git(root, "rev-parse", "HEAD") != commit:
        msg = "HEAD is not the exact peeled version tag; check out refs/tags/vX.Y.Z."
        raise ReleaseError(msg)
    _git(root, "merge-base", "--is-ancestor", commit, "origin/main")
    title = _git(root, "log", "-1", "--format=%s")
    if not re.fullmatch(rf"chore\(release\): {re.escape(tag)}(?: \(#[0-9]+\))?", title):
        msg = "The tag is not a reviewed release commit."
        raise ReleaseError(msg)
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project.get("name") != "heinzel" or project.get("version") != version:
        msg = "The tag and core project name/version disagree."
        raise ReleaseError(msg)
    if (
        release.get("tag_name") != tag
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or not isinstance(release.get("published_at"), str)
        or release.get("html_url") != f"https://github.com/{REPOSITORY}/releases/tag/{tag}"
    ):
        msg = "Publish and verify the genuine GitHub release before authorizing its PyPI upload."
        raise ReleaseError(msg)
    return commit


def _metadata(data: bytes, version: str) -> None:
    metadata = BytesParser().parsebytes(data)
    if metadata.get_all("Name") != ["heinzel"] or metadata.get_all("Version") != [version]:
        msg = "The distribution metadata is not the exact core release."
        raise ReleaseError(msg)


def _wheel(path: Path, version: str) -> None:
    if path.name != f"heinzel-{version}-py3-none-any.whl":
        msg = "Expected exactly the core's universal wheel for this version."
        raise ReleaseError(msg)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(name not in names for name in REQUIRED):
            msg = "The wheel has duplicate paths or lacks the browser app, notifier, changelog or core package."
            raise ReleaseError(msg)
        metadata = f"heinzel-{version}.dist-info/METADATA"
        if metadata not in names:
            msg = "The core wheel metadata is missing."
            raise ReleaseError(msg)
        _metadata(archive.read(metadata), version)
        entries = archive.read(f"heinzel-{version}.dist-info/entry_points.txt").decode()
        if not re.search(r"^hz\s*=\s*heinzel\.heads\.cli:main\s*$", entries, re.MULTILINE):
            msg = "The wheel does not provide the expected hz command."
            raise ReleaseError(msg)
        if any(stat.S_IFMT(archive.getinfo(name).external_attr >> 16) not in {0, stat.S_IFREG} for name in REQUIRED):
            msg = "Required wheel payloads must be regular files."
            raise ReleaseError(msg)
        if not (archive.getinfo(NOTIFIER).external_attr >> 16) & 0o111:
            msg = "The packaged notifier is not executable."
            raise ReleaseError(msg)
        if not archive.read(NOTIFIER).startswith(b"\xca\xfe\xba\xbe"):
            msg = "The packaged notifier is not the universal macOS executable."
            raise ReleaseError(msg)
        if any(not archive.read(name) for name in REQUIRED):
            msg = "A required packaged payload is empty."
            raise ReleaseError(msg)


def _sdist(path: Path, version: str) -> None:
    prefix = f"heinzel-{version}/"
    if path.name != f"heinzel-{version}.tar.gz":
        msg = "Expected exactly the core source distribution for this version."
        raise ReleaseError(msg)
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
        required = [f"{prefix}src/{name}" for name in REQUIRED if name != "heinzel/CHANGELOG.md"]
        required += [f"{prefix}CHANGELOG.md", f"{prefix}PKG-INFO", f"{prefix}pyproject.toml"]
        if len(names) != len(set(names)) or any(name not in names for name in required):
            msg = "The source distribution is incomplete or contains duplicate paths."
            raise ReleaseError(msg)
        for name in required:
            member = archive.getmember(name)
            stream = archive.extractfile(member) if member.isfile() else None
            if stream is None:
                msg = "Required source payloads must be nonempty regular files."
                raise ReleaseError(msg)
            with stream:
                data = stream.read()
            if not data:
                msg = "Required source payloads must be nonempty regular files."
                raise ReleaseError(msg)
            if name.endswith("/PKG-INFO"):
                _metadata(data, version)
            elif name.endswith("/pyproject.toml"):
                project = tomllib.loads(data.decode())["project"]
                if project.get("name") != "heinzel" or project.get("version") != version:
                    msg = "The source distribution project identity disagrees with the release."
                    raise ReleaseError(msg)


def verify_artifacts(directory: Path, tag: str, commit: str) -> dict[str, object]:
    """Accept just the complete core wheel and sdist, returning their immutable identity and digests.

    Raises:
        ReleaseError: If filenames, metadata, packaged payloads or source identity are wrong.
    """
    version = version_for(tag)
    if not COMMIT.fullmatch(commit):
        msg = "The verified source commit is required."
        raise ReleaseError(msg)
    files = sorted(directory.iterdir())
    if len(files) != ARTIFACT_COUNT or any(path.is_symlink() or not path.is_file() for path in files):
        msg = "The PyPI artifact directory must contain only one core wheel and one source distribution."
        raise ReleaseError(msg)
    wheel = next((path for path in files if path.suffix == ".whl"), None)
    sdist = next((path for path in files if path.name.endswith(".tar.gz")), None)
    if wheel is None or sdist is None:
        msg = "Both the core wheel and source distribution are required."
        raise ReleaseError(msg)
    _wheel(wheel, version)
    _sdist(sdist, version)
    return {
        "schema": 1,
        "project": "heinzel",
        "version": version,
        "commit": commit,
        "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
    }


def main(arguments: list[str]) -> int:
    """Verify source or artifacts; output is a job result or the artifact manifest.

    Raises:
        ReleaseError: If the release does not meet the publishing boundary.
    """
    match arguments:
        case ["source", tag, release_file]:
            release = json.loads(Path(release_file).read_text(encoding="utf-8"))
            if not isinstance(release, dict):
                msg = "Expected GitHub release metadata."
                raise ReleaseError(msg)
            print(f"commit={verify_source(ROOT, tag, release)}")
        case ["artifacts", tag, commit, directory, manifest]:
            result = verify_artifacts(Path(directory), tag, commit)
            Path(manifest).write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
        case _:
            print(
                "Usage: pypi_release.py source TAG RELEASE.json | artifacts TAG COMMIT DIST MANIFEST.json",
                file=sys.stderr,
            )
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
