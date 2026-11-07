"""Retrieve and verify the desktop assets from the official Core release."""

import hashlib
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from http.client import HTTPException, HTTPMessage
from pathlib import Path
from typing import IO, Any, Literal, cast, override
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from heinzel import HeinzelError
from heinzel.heads._desktop_archive import MAX_ARCHIVE_BYTES
from heinzel.heads._desktop_report import SILENT, Advance, Reporter

REPOSITORY = "helmut-hoffer-von-ankershoffen/heinzel"
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
MANIFEST_NAME = "desktop-artifact-manifest.json"
CORE_MANIFEST_NAME = "artifact-manifest.json"
PROOF_NAME = "provenance.sigstore.json"
API_TIMEOUT = 20
"""Seconds GitHub's API may stay silent before the request counts as stalled."""
DOWNLOAD_TIMEOUT = 30
"""Seconds a release asset download may stay silent before it counts as stalled."""
VERIFY_TIMEOUT = 60
"""Seconds the GitHub CLI may take to verify one attestation."""
ATTEMPTS = 3
RETRY_PAUSE = 1.0
LATER = "Run `hz system update` to look for a newer release, or try again later."
HINT = "Check your network connection and proxy settings, then run hz app install again."


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


class _NotFoundError(HeinzelError):
    """GitHub has no such resource."""


def _reason(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}"
    cause = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(cause, TimeoutError):
        return "no answer in time"
    return str(cause) or type(cause).__name__


def _retrying[T](action: Callable[[], T], what: str, reporter: Reporter) -> T:
    """Run a network step; transient failures are retried with a visible note, the rest fail with advice.

    Raises:
        _NotFoundError: If GitHub answers 404.
        HeinzelError: If GitHub refuses, or the network still fails after the last attempt.
    """
    failure: Exception = TimeoutError()
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return action()
        except HTTPError as exc:
            exc.close()
            if exc.code == 404:  # noqa: PLR2004 - HTTP status
                raise _NotFoundError(what) from exc
            if exc.code in {401, 403, 429}:
                msg = (
                    f"GitHub refused {what} (HTTP {exc.code}), probably its limit for requests without a login. "
                    "Wait a few minutes, then run hz app install again."
                )
                raise HeinzelError(msg) from exc
            failure = exc
        except (OSError, HTTPException) as exc:
            failure = exc
        if attempt == ATTEMPTS:
            break
        reporter.note(f"{what} failed ({_reason(failure)}); retrying, attempt {attempt + 1} of {ATTEMPTS}.")
        time.sleep(RETRY_PAUSE)
    msg = f"{what} failed: {_reason(failure)}. {HINT}"
    raise HeinzelError(msg) from failure


def _api(resource: str, reporter: Reporter = SILENT) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{REPOSITORY}/{resource}"
    request = Request(url, headers={"Accept": "application/vnd.github+json"})

    def fetch() -> bytes:
        with build_opener(_ReleaseRedirects()).open(request, timeout=API_TIMEOUT) as response:
            return cast("bytes", response.read(4 * 1024 * 1024 + 1))

    data = _retrying(fetch, "Asking GitHub about the release", reporter)
    if len(data) > 4 * 1024 * 1024:
        msg = "The GitHub release identity response is too large."
        raise HeinzelError(msg)
    return TypeAdapter(dict[str, Any]).validate_json(data, strict=True)


def _release_assets(tag: str, names: set[str], reporter: Reporter = SILENT) -> dict[str, tuple[int, str]]:
    try:
        release = _api(f"releases/tags/{tag}", reporter)
    except _NotFoundError as exc:
        msg = f"No desktop app is published for hz {tag.removeprefix('v')} yet: GitHub has no release {tag}. {LATER}"
        raise HeinzelError(msg) from exc
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
    if not assets:
        msg = f"No desktop app is published for hz {tag.removeprefix('v')} yet: release {tag} has no macOS app. {LATER}"
        raise HeinzelError(msg)
    if assets.keys() != names:
        msg = "The official release does not provide the complete desktop artifact."
        raise HeinzelError(msg)
    return assets


def _digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _check_producer(core: _CoreManifest, tag: str, reporter: Reporter = SILENT) -> None:
    producer = core.producer
    main = producer.source_ref == "refs/heads/main"
    workflow = ".github/workflows/release-tag.yml" if main else ".github/workflows/publish.yml"
    if producer.source_ref not in {"refs/heads/main", f"refs/tags/{tag}"} or producer.workflow != workflow:
        msg = "The desktop release was built outside the official release workflow."
        raise HeinzelError(msg)
    branch = _api("branches/main", reporter)
    run = _api(f"actions/runs/{producer.run_id}/attempts/{producer.attempt}", reporter)
    tagged = _api(f"commits/{tag}", reporter)
    ancestry = _api(f"compare/{core.commit}...main?per_page=1", reporter)
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


def _ignore(amount: int) -> None:
    del amount


def _download(
    version: str, target: Path, limit: int, reporter: Reporter = SILENT, advance: Advance | None = None
) -> None:
    url = f"https://github.com/{REPOSITORY}/releases/download/v{version}/{target.name}"
    count: Advance = advance or _ignore

    def fetch() -> None:
        target.unlink(missing_ok=True)
        sent = 0
        try:
            with (
                build_opener(_ReleaseRedirects()).open(url, timeout=DOWNLOAD_TIMEOUT) as response,
                target.open("xb") as output,
            ):
                remaining = limit
                while chunk := response.read(min(1024 * 1024, remaining + 1)):
                    remaining -= len(chunk)
                    if remaining < 0:
                        msg = "The desktop release asset exceeds the allowed size."
                        raise HeinzelError(msg)  # noqa: TRY301 - aborts the attempt, never retried
                    output.write(chunk)
                    sent += len(chunk)
                    count(len(chunk))
        except Exception:
            count(-sent)
            raise

    try:
        _retrying(fetch, f"Downloading {target.name}", reporter)
    except _NotFoundError as exc:
        msg = f"No desktop app is published for hz {version} yet: {target.name} is missing from its release. {LATER}"
        raise HeinzelError(msg) from exc


def _verify(gh: str, asset: Path, proof: Path, core: _CoreManifest) -> None:
    try:
        result = _run_gh(gh, asset, proof, core)
    except subprocess.TimeoutExpired as exc:
        msg = (
            f"GitHub CLI did not finish verifying {asset.name} within {VERIFY_TIMEOUT} seconds. "
            f"Check your network connection and that `gh auth status` works, then run hz app install again."
        )
        raise HeinzelError(msg) from exc
    producer = core.producer
    invocation = f"https://github.com/{REPOSITORY}/actions/runs/{producer.run_id}/attempts/{producer.attempt}"
    verified = [] if result.returncode else TypeAdapter(list[dict[str, Any]]).validate_json(result.stdout, strict=True)
    if not any(
        entry["verificationResult"]["signature"]["certificate"].get("runInvocationURI") == invocation
        and entry["verificationResult"]["statement"]["predicate"]["runDetails"]["metadata"].get("invocationId")
        == invocation
        for entry in verified
    ):
        lines = result.stderr.decode(errors="replace").strip().splitlines()
        detail = f" GitHub CLI said: {lines[-1]}" if result.returncode and lines else ""
        msg = f"The official desktop release provenance could not be verified.{detail}"
        raise HeinzelError(msg)


def _run_gh(gh: str, asset: Path, proof: Path, core: _CoreManifest) -> subprocess.CompletedProcess[bytes]:
    commit, producer = core.commit, core.producer
    return subprocess.run(  # noqa: S603 - fixed CLI, release asset and validated commit
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
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=VERIFY_TIMEOUT,
    )


def fetch_archive(version: str, directory: Path, reporter: Reporter = SILENT) -> Path:
    """Download the exact stable version and authenticate both desktop artifacts.

    Raises:
        HeinzelError: If the official release is unavailable or any identity check fails.
    """
    if not VERSION.fullmatch(version):
        msg = "Desktop installation requires a published stable Heinzel version."
        raise HeinzelError(msg)
    gh = shutil.which("gh")
    if gh is None:
        msg = (
            "Install GitHub CLI (https://cli.github.com) to verify the desktop release, then run hz app install again."
        )
        raise HeinzelError(msg)
    manifest_path = directory / MANIFEST_NAME
    core_path = directory / CORE_MANIFEST_NAME
    proof = directory / PROOF_NAME
    archive = directory / f"heinzel-desktop-{version}-darwin-arm64.tar.gz"
    paths = (manifest_path, core_path, proof, archive)
    names = {path.name for path in paths}
    tag = f"v{version}"
    try:
        with reporter.phase(f"Resolving release {tag}"):
            assets = _release_assets(tag, names, reporter)
        with reporter.phase("Downloading release manifest and provenance"):
            _download(version, manifest_path, 16 * 1024, reporter)
            _download(version, proof, 2 * 1024 * 1024, reporter)
            _download(version, core_path, 32 * 1024, reporter)
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
        with reporter.phase("Verifying manifest provenance (GitHub attestation)"):
            _verify(gh, core_path, proof, core)
            _verify(gh, manifest_path, proof, core)
        with reporter.phase("Checking the release came from the protected build"):
            _check_producer(core, tag, reporter)
        with reporter.phase(
            f"Downloading the desktop app ({manifest.archive.size / 1e6:.0f} MB)", manifest.archive.size
        ) as advance:
            _download(version, archive, manifest.archive.size, reporter, advance)
        with reporter.phase("Verifying checksum"):
            if archive.stat().st_size != manifest.archive.size or _digest(archive) != manifest.archive.sha256:
                msg = "The downloaded desktop bundle differs from its release manifest."
                raise HeinzelError(msg)
        with reporter.phase("Verifying app provenance (GitHub attestation)"):
            _verify(gh, archive, proof, core)
        with reporter.phase("Confirming the release assets did not change"):
            if any(_digest(path) != assets[path.name][1] for path in paths) or assets != _release_assets(
                tag, names, reporter
            ):
                msg = "The desktop release assets changed or differ from their published identities."
                raise HeinzelError(msg)
    except (OSError, ValueError, KeyError, TypeError, ValidationError, subprocess.SubprocessError) as exc:
        msg = f"Could not retrieve and verify the official desktop bundle for hz {version}: {type(exc).__name__}."
        raise HeinzelError(msg) from exc
    return archive
