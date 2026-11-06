"""Retrieve and verify the desktop assets from the official Core release."""

import hashlib
import json
import re
import shutil
import subprocess
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Any, Literal, override
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from heinzel import HeinzelError
from heinzel.heads._desktop_archive import MAX_ARCHIVE_BYTES

REPOSITORY = "helmut-hoffer-von-ankershoffen/heinzel"
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
MANIFEST_NAME = "desktop-artifact-manifest.json"
CORE_MANIFEST_NAME = "artifact-manifest.json"
PROOF_NAME = "provenance.sigstore.json"


class _Archive(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0, le=MAX_ARCHIVE_BYTES)


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = Field(alias="schema")
    version: str
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    platform: Literal["darwin"]
    arch: Literal["arm64"]
    archive: _Archive


class _Producer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: int = Field(gt=0)
    attempt: int = Field(gt=0)
    source_ref: str
    workflow: str


class _CoreManifest(BaseModel):
    model_config = ConfigDict(strict=True)

    version: str
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    producer: _Producer


def _api(resource: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{REPOSITORY}/{resource}"
    request = Request(url, headers={"Accept": "application/vnd.github+json"})
    with build_opener(_ReleaseRedirects()).open(request, timeout=20) as response:
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        msg = "The GitHub release identity response is too large."
        raise HeinzelError(msg)
    result = json.loads(data)
    if not isinstance(result, dict):
        msg = "GitHub did not return a release identity object."
        raise HeinzelError(msg)
    return result


def _release_assets(tag: str, names: set[str]) -> dict[str, tuple[int, str]]:
    release = _api(f"releases/tags/{tag}")
    if release.get("tag_name") != tag or release.get("draft") is not False or release.get("prerelease") is not False:
        msg = "The requested desktop release is not published as stable."
        raise HeinzelError(msg)
    assets: dict[str, tuple[int, str]] = {}
    for asset in release["assets"]:
        name = asset["name"]
        if name not in names:
            continue
        identifier, digest = asset["id"], asset["digest"]
        if (
            name in assets
            or type(identifier) is not int
            or identifier <= 0
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        ):
            msg = "The official desktop release asset identity is invalid or ambiguous."
            raise HeinzelError(msg)
        assets[name] = (identifier, digest.removeprefix("sha256:"))
    if assets.keys() != names:
        msg = "The official release does not provide the complete desktop artifact."
        raise HeinzelError(msg)
    return assets


def _digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _check_producer(core: _CoreManifest, tag: str) -> None:
    producer = core.producer
    main = producer.source_ref == "refs/heads/main"
    workflow = ".github/workflows/release-tag.yml" if main else ".github/workflows/publish.yml"
    if producer.source_ref not in {"refs/heads/main", f"refs/tags/{tag}"} or producer.workflow != workflow:
        msg = "The desktop release was built outside the official release workflow."
        raise HeinzelError(msg)
    branch = _api("branches/main")
    run = _api(f"actions/runs/{producer.run_id}/attempts/{producer.attempt}")
    tagged = _api(f"commits/{tag}")
    ancestry = _api(f"compare/{core.commit}...main?per_page=1")
    if (
        branch.get("protected") is not True
        or tagged.get("sha") != core.commit
        or ancestry.get("status") not in {"ahead", "identical"}
        or run.get("id") != producer.run_id
        or run.get("run_attempt") != producer.attempt
        or run.get("head_sha") != core.commit
        or run.get("path") != workflow
        or run.get("head_branch") != ("main" if main else tag)
        or run.get("repository", {}).get("full_name") != REPOSITORY
        or run.get("event") not in ({"push", "workflow_dispatch"} if main else {"push"})
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
    ):
        msg = "The desktop release does not match a successful protected Core release."
        raise HeinzelError(msg)


class _ReleaseRedirects(HTTPRedirectHandler):
    @override
    def redirect_request(
        self, req: Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> Request:
        del fp, code, msg, headers
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.netloc not in {"github.com", "release-assets.githubusercontent.com"}:
            detail = "The desktop release redirected outside the official asset hosts."
            raise HeinzelError(detail)
        return Request(newurl, method=req.get_method())  # noqa: S310 - official HTTPS host checked above


def _download(version: str, target: Path, limit: int) -> None:
    url = f"https://github.com/{REPOSITORY}/releases/download/v{version}/{target.name}"
    with build_opener(_ReleaseRedirects()).open(url, timeout=30) as response, target.open("xb") as output:
        remaining = limit
        while chunk := response.read(min(1024 * 1024, remaining + 1)):
            remaining -= len(chunk)
            if remaining < 0:
                msg = "The desktop release asset exceeds the allowed size."
                raise HeinzelError(msg)
            output.write(chunk)


def _verify(gh: str, asset: Path, proof: Path, core: _CoreManifest) -> None:
    commit, producer = core.commit, core.producer
    result = subprocess.run(  # noqa: S603 - fixed CLI, release asset and validated commit
        [
            gh,
            "attestation",
            "verify",
            str(asset),
            "--bundle",
            str(proof),
            "--repo",
            REPOSITORY,
            "--signer-workflow",
            f"{REPOSITORY}/.github/workflows/publish.yml",
            "--signer-digest",
            commit,
            "--source-digest",
            commit,
            "--source-ref",
            producer.source_ref,
            "--deny-self-hosted-runners",
            "--format",
            "json",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=60,
    )
    invocation = f"https://github.com/{REPOSITORY}/actions/runs/{producer.run_id}/attempts/{producer.attempt}"
    verified = [] if result.returncode else json.loads(result.stdout)
    if not any(
        entry["verificationResult"]["signature"]["certificate"].get("runInvocationURI") == invocation
        and entry["verificationResult"]["statement"]["predicate"]["runDetails"]["metadata"].get("invocationId")
        == invocation
        for entry in verified
    ):
        msg = "The official desktop release provenance could not be verified."
        raise HeinzelError(msg)


def fetch_archive(version: str, directory: Path) -> Path:
    """Download the exact stable version and authenticate both desktop artifacts.

    Raises:
        HeinzelError: If the official release is unavailable or any identity check fails.
    """
    if not VERSION.fullmatch(version):
        msg = "Desktop installation requires a published stable Heinzel version."
        raise HeinzelError(msg)
    gh = shutil.which("gh")
    if gh is None:
        msg = "Install GitHub CLI to verify the desktop release."
        raise HeinzelError(msg)
    manifest_path = directory / MANIFEST_NAME
    core_path = directory / CORE_MANIFEST_NAME
    proof = directory / PROOF_NAME
    archive = directory / f"heinzel-desktop-{version}-darwin-arm64.tar.gz"
    paths = (manifest_path, core_path, proof, archive)
    names = {path.name for path in paths}
    try:
        assets = _release_assets(f"v{version}", names)
        _download(version, manifest_path, 16 * 1024)
        _download(version, proof, 2 * 1024 * 1024)
        _download(version, core_path, 32 * 1024)
        core = _CoreManifest.model_validate_json(core_path.read_bytes())
        manifest = _Manifest.model_validate_json(manifest_path.read_bytes())
        if (
            core.version != version
            or manifest.version != version
            or manifest.commit != core.commit
            or manifest.archive.name != archive.name
        ):
            msg = "The desktop release manifest names a different artifact."
            raise HeinzelError(msg)
        _verify(gh, core_path, proof, core)
        _verify(gh, manifest_path, proof, core)
        _check_producer(core, f"v{version}")
        _download(version, archive, manifest.archive.size)
        if archive.stat().st_size != manifest.archive.size or _digest(archive) != manifest.archive.sha256:
            msg = "The downloaded desktop bundle differs from its release manifest."
            raise HeinzelError(msg)
        _verify(gh, archive, proof, core)
        if any(_digest(path) != assets[path.name][1] for path in paths) or assets != _release_assets(
            f"v{version}", names
        ):
            msg = "The desktop release assets changed or differ from their published identities."
            raise HeinzelError(msg)
    except (OSError, ValueError, KeyError, TypeError, ValidationError, subprocess.SubprocessError) as exc:
        msg = f"Could not retrieve and verify the official desktop bundle for hz {version}."
        raise HeinzelError(msg) from exc
    return archive
