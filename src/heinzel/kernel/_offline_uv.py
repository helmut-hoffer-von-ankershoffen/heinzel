"""Publisher-authenticated installed uv bytes copied before offline removal (ADR 0131)."""

import base64
import csv
import hashlib
import io
import os
import platform
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._private_control import private_directory
from heinzel.kernel._tool_uninstall import owned_file, physical_directory

VERSION = "0.13.7"
# Independent hashes of native bytes and WHEEL from Astral's archive-hash-verified PyPI wheels.
# Provenance: https://pypi.org/pypi/uv/0.13.7/json ; dependency pins alone are not executable trust.
ARTIFACTS = {
    "darwin-arm64": (
        "0f333833a6d011710d3edc0e45b55d3d934aef15bb83a25b29848c241ac513b4",
        "53ddbba522ef58787979aa9c379bfbd8274c9be1d4585df8e1f28b7bc1e148e5",
    ),
    "darwin-x86_64": (
        "b345904c70c5bb715cda5a58cb74b6c53158346e806258e270a774fdaa3d7739",
        "97276f09c51f34c8d9b07ed37036906e09e07615ea9b8c2cf9211fd737cdb5c9",
    ),
    "linux-arm64": (
        "553b3a3a31484ef9e4f220085ca672cc71c851f92a92d984646a9d1c814b02c3",
        "74657385fd3918b2c8171d8b51464275726aaae1cfe74c7c28dd139bf1a6760d",
    ),
    "linux-x86_64": (
        "4b5e59bc557ff2cd70934beb59d0eaa92820702853d49f0066b50aa11a8f0c52",
        "9d1b8ac16650753920001d727880fc6720369d6190678253e69694e8e84c4429",
    ),
}
METADATA_HASH = "507c23b3a9f1163f536cf22e673779defab68318c82cff8d93551d773c056d62"
LICENSE_HASHES = {
    "LICENSE-APACHE": "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4",
    "LICENSE-MIT": "860e3d7a86b84e6a7012c7a635fc64df475cebc6cce34dfeb73a5982ec58176c",
}
MAX_BINARY = 128 * 1024 * 1024


def fsync_directory(path: Path) -> None:
    """Synchronize a physical owned directory after a durable record or cleanup change."""
    physical_directory(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def selection() -> tuple[str, str]:
    """Select independently pinned native bytes; unsupported platforms fail before service effects.

    Raises:
        HeinzelError: If this platform has no compatible pinned remover.
    """
    machine = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x86_64"}.get(platform.machine())
    key = f"{platform.system().lower()}-{machine}"
    if key not in ARTIFACTS:
        msg = (
            "This platform has no verified offline Heinzel remover. Inspect its local installer without deleting data."
        )
        raise HeinzelError(msg)
    return ARTIFACTS[key]


def _installed_bytes(prefix: Path) -> tuple[bytes, list[bytes]]:
    from heinzel.kernel._installed_metadata import inspect_versions  # noqa: PLC0415 - local static metadata only

    if inspect_versions(prefix, ["uv"]) != {"uv": VERSION}:
        msg = "The pinned offline remover dependency is missing or ambiguous; no service was stopped."
        raise HeinzelError(msg)
    matches = list(prefix.glob(f"lib/python*/site-packages/uv-{VERSION}.dist-info"))
    if len(matches) != 1:
        msg = "The pinned offline remover metadata is missing or ambiguous; no service was stopped."
        raise HeinzelError(msg)
    metadata = matches[0]
    binary_hash, wheel_hash = selection()
    pinned = {"METADATA": METADATA_HASH, "WHEEL": wheel_hash}
    pinned.update({f"licenses/{name}": digest for name, digest in LICENSE_HASHES.items()})
    files = {name: owned_file(metadata / name).content for name in pinned}
    if any(hashlib.sha256(files[name]).hexdigest() != digest for name, digest in pinned.items()):
        msg = "The offline remover distribution metadata changed; no service was stopped."
        raise HeinzelError(msg)
    if os.path.lexists(metadata / "direct_url.json"):
        msg = "The offline remover has an unsupported installation source; no service was stopped."
        raise HeinzelError(msg)
    binary = owned_file(prefix / "bin/uv", limit=MAX_BINARY).content
    if hashlib.sha256(binary).hexdigest() != binary_hash:
        msg = "The installed offline remover does not match its publisher hash or architecture; no service was stopped."
        raise HeinzelError(msg)
    records = list(csv.reader(io.StringIO(owned_file(metadata / "RECORD").content.decode("utf-8"))))
    expected_hash = "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(binary_hash)).decode().rstrip("=")
    if [row for row in records if row and row[0] == "../../../bin/uv"] != [
        ["../../../bin/uv", expected_hash, str(len(binary))]
    ]:
        msg = "The installed offline remover has no exact owned RECORD entry; no service was stopped."
        raise HeinzelError(msg)
    return binary, [files[f"licenses/{name}"] for name in LICENSE_HASHES]


@dataclass(frozen=True)
class VerifiedUV:
    """A publisher-verified binary copied to a private physical recovery directory."""

    path: Path
    digest: str
    identity: tuple[int, ...]

    def verify(self) -> None:
        """Reopen and recheck this exact private inode and its bytes before execution.

        Raises:
            HeinzelError: If the copy was replaced, redirected, changed or made writable by another user.
            OSError: If the private copy is unavailable.
        """
        captured = owned_file(self.path, limit=MAX_BINARY)
        if captured.identity != self.identity or hashlib.sha256(captured.content).hexdigest() != self.digest:
            msg = "The private offline remover changed; no further removal was attempted."
            raise HeinzelError(msg)


def stage(prefix: Path, directory: Path) -> VerifiedUV:
    """Verify installed dependency bytes and stage a protected remover before stopping any service.

    Raises:
        HeinzelError: If metadata, artifact, architecture or the private destination cannot be verified.
        OSError: If the installed dependency is absent or a physical copy cannot be saved.
    """
    binary, licenses = _installed_bytes(prefix)
    private_directory(directory)
    target = directory / f"uv-{secrets.token_hex(8)}"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o500)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(binary)
        stream.flush()
        os.fsync(stream.fileno())
    notice = directory / "LICENSE.uv"
    descriptor = os.open(notice, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(b"\n\n".join(licenses))
        stream.flush()
        os.fsync(stream.fileno())
    fsync_directory(directory)
    captured = owned_file(target, limit=MAX_BINARY)
    prepared = VerifiedUV(target, hashlib.sha256(binary).hexdigest(), captured.identity)
    prepared.verify()
    try:
        result = subprocess.run(  # noqa: S603 - publisher-pinned bytes copied to a protected private inode
            [str(target), "--offline", "--no-config", "--no-python-downloads", "--version"],
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        msg = "The pinned offline remover cannot run on this installation; no service was stopped."
        raise HeinzelError(msg) from error
    if not result.stdout.startswith(f"uv {VERSION} ") and result.stdout.strip() != f"uv {VERSION}":
        msg = "The offline remover could not identify its pinned version; no service was stopped."
        raise HeinzelError(msg)
    prepared.verify()
    return prepared
