# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Verify a genuine core release and its complete distributions before an isolated PyPI upload."""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from configparser import ConfigParser
from configparser import Error as ConfigError
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "helmut-hoffer-von-ankershoffen/heinzel"
TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
ARTIFACT_COUNT = 2
MAX_MANIFEST_BYTES = 64 * 1024
COMMIT = re.compile(r"[0-9a-f]{40}")
NOTIFIER = "heinzel/modules/system/notify/macos/HeinzelNotifier.app/Contents/MacOS/notifier"
REQUIRED = (
    "_heinzel_launcher.py",
    "heinzel/__init__.py",
    "heinzel/CHANGELOG.md",
    "heinzel/heads/web/static/index.html",
    NOTIFIER,
)
CONSOLE_SCRIPTS = {
    "heinzel": "_heinzel_launcher:main",
    "hz": "_heinzel_launcher:cli",
    "hz-install": "_heinzel_launcher:install",
}


class ReleaseError(ValueError):
    """The source or distributions are not a verifiable core release."""


class _EntryParser(ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


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


def verify_checkout(root: Path, tag: str) -> str:
    """Bind the exact tag, main ancestry, reviewed release title and project identity.

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
    return commit


def verify_source(root: Path, tag: str, release: dict[str, object]) -> str:
    """Bind the reviewed checkout to the genuine published GitHub release.

    Raises:
        ReleaseError: If the source or published release identity disagrees.
    """
    commit = verify_checkout(root, tag)
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


def _console_scripts(archive: zipfile.ZipFile, version: str) -> None:
    try:
        entries = archive.read(f"heinzel-{version}.dist-info/entry_points.txt").decode("utf-8")
        parser = _EntryParser(interpolation=None, strict=True)
        parser.read_string(entries)
        commands = dict(parser.items("console_scripts", raw=True))
    except (KeyError, UnicodeError, ConfigError) as error:
        msg = "The wheel does not provide the expected console scripts."
        raise ReleaseError(msg) from error
    if parser.defaults() or commands != CONSOLE_SCRIPTS:
        msg = "The wheel does not provide the expected console scripts."
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
        _console_scripts(archive, version)
        if not stat.S_ISREG(archive.getinfo("_heinzel_launcher.py").external_attr >> 16):
            msg = "The packaged launcher must be a regular file."
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


def producer_manifest(directory: Path, tag: str, commit: str) -> dict[str, object]:
    """Bind the distribution digests to the official producer's exact invocation.

    Raises:
        ReleaseError: If this is not an official final release producer invocation.
    """
    manifest = verify_artifacts(directory, tag, commit)
    source_ref = os.environ["GITHUB_REF"]
    workflow = (
        ".github/workflows/release-tag.yml" if source_ref == "refs/heads/main" else ".github/workflows/publish.yml"
    )
    if (
        os.environ["GITHUB_REPOSITORY"] != REPOSITORY
        or os.environ["GITHUB_SHA"] != commit
        or source_ref not in {"refs/heads/main", f"refs/tags/{tag}"}
        or os.environ["GITHUB_WORKFLOW_REF"] != f"{REPOSITORY}/{workflow}@{source_ref}"
    ):
        msg = "The artifacts were not built by an official release producer."
        raise ReleaseError(msg)
    run_id, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    if not re.fullmatch(r"[1-9][0-9]*", run_id) or not re.fullmatch(r"[1-9][0-9]*", attempt):
        msg = "The exact producer invocation is required."
        raise ReleaseError(msg)
    manifest["producer"] = {
        "run_id": int(run_id),
        "attempt": int(attempt),
        "source_ref": source_ref,
        "workflow": workflow,
    }
    return manifest


def verify_producer(manifest: dict, tag: str, commit: str, run: dict, branch: dict) -> None:
    """Verify the signed producer identity against the current official Actions run.

    Raises:
        ReleaseError: If the producer is incomplete, substituted or outside protected main.
    """
    producer = manifest.get("producer", {})
    source_ref = producer.get("source_ref")
    workflow = (
        ".github/workflows/release-tag.yml" if source_ref == "refs/heads/main" else ".github/workflows/publish.yml"
    )
    if (
        branch.get("name") != "main"
        or branch.get("protected") is not True
        or manifest.get("commit") != commit
        or manifest.get("version") != version_for(tag)
        or source_ref not in {"refs/heads/main", f"refs/tags/{tag}"}
        or producer.get("workflow") != workflow
        or type(producer.get("run_id")) is not int
        or type(producer.get("attempt")) is not int
        or producer["run_id"] <= 0
        or producer["attempt"] <= 0
        or run.get("id") != producer["run_id"]
        or run.get("run_attempt") != producer["attempt"]
        or run.get("repository", {}).get("full_name") != REPOSITORY
        or run.get("head_sha") != commit
        or run.get("path") != workflow
        or run.get("head_branch") != source_ref.removeprefix("refs/heads/").removeprefix("refs/tags/")
        or run.get("event") not in ({"push", "workflow_dispatch"} if source_ref == "refs/heads/main" else {"push"})
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
    ):
        msg = "The official protected-main producer run does not match the release."
        raise ReleaseError(msg)


def verify_assets(release: dict, directory: Path, tag: str, manifest: dict) -> None:
    """Bind downloaded asset names, GitHub identities and SHA256 to the signed manifest.

    Raises:
        ReleaseError: If an asset is missing, replaced, ambiguous or changed.
    """
    version = version_for(tag)
    expected = {f"heinzel-{version}-py3-none-any.whl", f"heinzel-{version}.tar.gz", "artifact-manifest.json"}
    if {path.name for path in directory.iterdir()} != expected:
        msg = "The downloaded release must contain only the signed manifest and exact core distributions."
        raise ReleaseError(msg)
    assets = release.get("assets", [])
    for name in expected:
        matching = [asset for asset in assets if asset.get("name") == name]
        path = directory / name
        if (
            len(matching) != 1
            or path.is_symlink()
            or not path.is_file()
            or matching[0].get("browser_download_url")
            != f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
            or type(matching[0].get("id")) is not int
            or matching[0]["id"] <= 0
            or matching[0].get("digest") != f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
            or (
                name != "artifact-manifest.json"
                and matching[0]["digest"] != f"sha256:{manifest.get('files', {}).get(name)}"
            )
        ):
            msg = "Published release assets are missing, substituted or have changed digests."
            raise ReleaseError(msg)


def verify_provenance(proofs: list, manifest: dict, name: str, digest: str) -> None:
    """Apply exact invocation policy to gh's cryptographically verified SLSA statements.

    Raises:
        ReleaseError: If verified provenance is not this exact official invocation and subject.
    """
    producer = manifest["producer"]
    invocation = f"https://github.com/{REPOSITORY}/actions/runs/{producer['run_id']}/attempts/{producer['attempt']}"
    for proof in proofs:
        result = proof.get("verificationResult", {})
        statement = result.get("statement", {})
        predicate = statement.get("predicate", {})
        definition = predicate.get("buildDefinition", {})
        workflow = definition.get("externalParameters", {}).get("workflow", {})
        if (
            statement.get("predicateType") == "https://slsa.dev/provenance/v1"
            and result.get("signature", {}).get("certificate")
            and result.get("verifiedTimestamps")
            and predicate.get("runDetails", {}).get("metadata", {}).get("invocationId") == invocation
            and workflow
            == {
                "repository": f"https://github.com/{REPOSITORY}",
                "path": producer["workflow"],
                "ref": producer["source_ref"],
            }
            and definition.get("internalParameters", {}).get("github", {}).get("runner_environment") == "github-hosted"
            and any(
                subject.get("name") == name and subject.get("digest") == {"sha256": digest}
                for subject in statement.get("subject", [])
            )
        ):
            return
    msg = "Verified provenance does not bind the exact release asset and producer invocation."
    raise ReleaseError(msg)


def _gh(*arguments: str) -> object:
    executable = shutil.which("gh")
    if executable is None:
        msg = "gh is required for cryptographic release verification."
        raise ReleaseError(msg)
    result = subprocess.run([executable, *arguments], check=True, capture_output=True, text=True, timeout=60)  # noqa: S603 - fixed gh commands and validated release identities
    return json.loads(result.stdout)


def verify_remote_tag(root: Path, tag: str, commit: str) -> None:
    """Reject a tag moved since checkout or while its release artifacts were verified.

    Raises:
        ReleaseError: If the current remote tag does not peel to the authorized source.
    """
    refs = dict(
        line.split()[::-1]
        for line in _git(
            root, "ls-remote", "--tags", "origin", f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"
        ).splitlines()
    )
    if refs.get(f"refs/tags/{tag}^{{}}", refs.get(f"refs/tags/{tag}")) != commit:
        msg = "The remote version tag changed from the authorized release source."
        raise ReleaseError(msg)


def _asset_identity(release: dict) -> dict:
    # Download counters may legitimately change; source and asset identity must remain unchanged.
    fields = ("id", "tag_name", "draft", "prerelease", "published_at", "html_url")
    asset_fields = ("id", "name", "digest", "size", "state", "browser_download_url", "created_at", "updated_at")
    return {
        **{key: release.get(key) for key in fields},
        "assets": [{key: asset.get(key) for key in asset_fields} for asset in release.get("assets", [])],
    }


def promote(directory: Path, tag: str, root: Path = ROOT) -> None:
    """Reverify the genuine signed GitHub bytes without rebuilding, and stage only core distributions.

    Raises:
        ReleaseError: If any source, producer, provenance, asset or payload check fails.
    """
    version_for(tag)
    published = _gh("api", f"repos/{REPOSITORY}/releases/tags/{tag}")
    commit = verify_source(root, tag, published)
    verify_remote_tag(root, tag, commit)
    manifest_path = directory / "artifact-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        msg = "The signed release manifest is missing or unsafe."
        raise ReleaseError(msg)
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except (ValueError, UnicodeError) as error:
        msg = "The signed release manifest is invalid."
        raise ReleaseError(msg) from error
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", "project", "version", "commit", "files", "producer"}
        or type(manifest.get("schema")) is not int
        or manifest["schema"] != 1
        or manifest.get("project") != "heinzel"
        or not isinstance(manifest.get("files"), dict)
        or not isinstance(manifest.get("producer"), dict)
    ):
        msg = "The signed release manifest has an unsupported identity or schema."
        raise ReleaseError(msg)
    producer = manifest.get("producer", {})
    run_id = producer.get("run_id")
    if type(run_id) is not int or run_id <= 0:
        msg = "The exact official producer run is required."
        raise ReleaseError(msg)
    run = _gh("api", f"repos/{REPOSITORY}/actions/runs/{run_id}")
    branch = _gh("api", f"repos/{REPOSITORY}/branches/main")
    verify_producer(manifest, tag, commit, run, branch)
    verify_assets(published, directory, tag, manifest)
    files = manifest.get("files", {})
    version = version_for(tag)
    if set(files) != {f"heinzel-{version}-py3-none-any.whl", f"heinzel-{version}.tar.gz"}:
        msg = "The signed manifest must contain exactly the core wheel and source distribution."
        raise ReleaseError(msg)
    for name in ["artifact-manifest.json", *sorted(files)]:
        path = directory / name
        proof = _gh(
            "attestation",
            "verify",
            str(path),
            "--repo",
            REPOSITORY,
            "--signer-workflow",
            f"{REPOSITORY}/.github/workflows/publish.yml",
            "--signer-digest",
            commit,
            "--source-digest",
            commit,
            "--source-ref",
            producer["source_ref"],
            "--deny-self-hosted-runners",
            "--format",
            "json",
        )
        verify_provenance(proof, manifest, name, hashlib.sha256(path.read_bytes()).hexdigest())
    dist = directory / "dist"
    dist.mkdir()
    for name in files:
        (directory / name).rename(dist / name)
    actual = verify_artifacts(dist, tag, commit)
    if manifest != {**actual, "producer": producer}:
        msg = "The signed release manifest differs from the verified distributions."
        raise ReleaseError(msg)
    # Re-read GitHub metadata after verification; asset replacement during retrieval must not be accepted.
    refreshed = _gh("api", f"repos/{REPOSITORY}/releases/tags/{tag}")
    verify_remote_tag(root, tag, commit)
    if _asset_identity(refreshed) != _asset_identity(published):
        msg = "The published release changed during promotion."
        raise ReleaseError(msg)


def main(arguments: list[str]) -> int:
    """Verify source or artifacts; output is a job result or the artifact manifest.

    Raises:
        ReleaseError: If the release does not meet the publishing boundary.
    """
    match arguments:
        case ["checkout", tag]:
            print(f"commit={verify_checkout(ROOT, tag)}")
        case ["promote", tag, directory]:
            promote(Path(directory), tag)
        case ["producer", tag, commit, directory, manifest]:
            result = producer_manifest(Path(directory), tag, commit)
            Path(manifest).write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
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
                "Usage: pypi_release.py checkout TAG | source TAG RELEASE.json | promote TAG DIRECTORY | "
                "{artifacts,producer} TAG COMMIT DIST MANIFEST.json",
                file=sys.stderr,
            )
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
