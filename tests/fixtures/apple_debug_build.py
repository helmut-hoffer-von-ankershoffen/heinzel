"""The Debug Mac app build that integration probes link against, prepared once before the timed sessions.

The build takes minutes on all of a 3-vCPU macOS runner. CI prepares it in its own step before pytest
(`make apple-integration-prepare`) and names it in `HZ_TEST_APPLE_DEBUG_BUILD`; a fixture reuses exactly that product
only when its manifest names the current sources and every product file still has the recorded content. Without a
prepared build (a local run) the fixture builds its own, as before.

Run `python -m tests.fixtures.apple_debug_build DIR` to prepare DIR.
"""

import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
from pathlib import Path

from heinzel.testing import scaled

ROOT = Path(__file__).resolve().parents[2]
PREPARED_VARIABLE = "HZ_TEST_APPLE_DEBUG_BUILD"
MANIFEST = "heinzel-prepared-build.json"
PRODUCTS = Path("Build/Products/Debug")
DYLIB = PRODUCTS / "Heinzel.app/Contents/MacOS/Heinzel.debug.dylib"
# Everything the Xcode project reads: its own folder and the artwork source its script phase converts.
SOURCES = ("apple", "web/src/components/heinzel.ts")
# The build is the heaviest CPU load of an integration session; anything beside it holds product deadlines.
YIELD_CPU = ("nice", "-n", "10")


class PreparedBuildError(AssertionError):
    """A prepared build that is not exactly the one the current sources produce."""


def app_build_command(derived: Path) -> list[str]:
    """The Debug app build into `derived`, at lowered scheduling priority."""
    return [
        *YIELD_CPU,
        "xcodebuild",
        "-project",
        "apple/Heinzel.xcodeproj",
        "-scheme",
        "Heinzel",
        "-configuration",
        "Debug",
        "-destination",
        f"platform=macOS,arch={platform.machine()}",
        "-derivedDataPath",
        str(derived),
        "CODE_SIGNING_ALLOWED=NO",
        "ENABLE_TESTABILITY=YES",
        "ONLY_ACTIVE_ARCH=YES",
        "build",
    ]


def source_identity(root: Path = ROOT) -> str:
    """The tracked build inputs as they are on disk, and the exact build command apart from its output folder."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", *SOURCES], cwd=root, check=True, capture_output=True, timeout=scaled(30)
    ).stdout
    digest = hashlib.sha256(b"heinzel-apple-debug-sources-v1\0")
    digest.update("\0".join(app_build_command(Path("DERIVED"))).encode() + b"\0")
    for name in sorted(filter(None, listed.split(b"\0"))):
        path = root / os.fsdecode(name)
        digest.update(name + b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"\0missing\0")
    return digest.hexdigest()


def product_identity(derived: Path) -> str:
    """Every file and link of the built products, by relative path, type and content; nothing outside `derived`."""
    products = derived / PRODUCTS
    if products.is_symlink() or not products.is_dir():
        msg = f"No built products in {derived}."
        raise PreparedBuildError(msg)
    digest = hashlib.sha256(b"heinzel-apple-debug-products-v1\0")
    for path in sorted(products.rglob("*")):
        info = path.lstat()
        relative = os.fsencode(path.relative_to(products)) + b"\0"
        if stat.S_ISLNK(info.st_mode):
            digest.update(b"L" + relative + os.fsencode(path.readlink()) + b"\0")
        elif stat.S_ISREG(info.st_mode):
            digest.update(b"F" + relative + f"{info.st_mode & 0o777:o}\0".encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        elif stat.S_ISDIR(info.st_mode):
            digest.update(b"D" + relative)
        else:
            msg = f"Unexpected file in the built products: {path}"
            raise PreparedBuildError(msg)
    return digest.hexdigest()


def build(derived: Path) -> Path:
    """Build the Debug app into `derived` under its unchanged deadline; return `derived`."""
    subprocess.run(app_build_command(derived), cwd=ROOT, check=True, timeout=scaled(240))
    return derived


def prepare(derived: Path, root: Path = ROOT) -> None:
    """Build into `derived` and record which sources it came from and what it produced."""
    derived.mkdir(parents=True, exist_ok=True)
    (derived / MANIFEST).unlink(missing_ok=True)
    sources = source_identity(root)
    build(derived)
    if source_identity(root) != sources:
        msg = "The Apple sources changed while they were built."
        raise PreparedBuildError(msg)
    manifest = {"format": 1, "sources": sources, "products": product_identity(derived)}
    (derived / MANIFEST).write_text(json.dumps(manifest, sort_keys=True))


def verified(prepared: str, root: Path = ROOT) -> Path:
    """The prepared build folder, after proving it is the build of the current sources, unchanged since.

    Raises:
        PreparedBuildError: If the folder, its manifest, the sources or any product file differ.
    """
    derived = Path(prepared)
    if not derived.is_absolute() or derived.is_symlink() or derived.resolve(strict=True) != derived:
        msg = f"{PREPARED_VARIABLE} must name a physical absolute folder, not {prepared!r}."
        raise PreparedBuildError(msg)
    info = derived.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        msg = f"The prepared build {derived} is not a private folder of this user."
        raise PreparedBuildError(msg)
    try:
        manifest = json.loads((derived / MANIFEST).read_text())
    except (OSError, ValueError) as exc:
        msg = f"The prepared build {derived} has no readable manifest."
        raise PreparedBuildError(msg) from exc
    if not isinstance(manifest, dict) or manifest.get("format") != 1:
        msg = f"The prepared build {derived} has an unknown manifest."
        raise PreparedBuildError(msg)
    if manifest.get("sources") != source_identity(root):
        msg = f"The prepared build {derived} was made from other Apple sources than this checkout's."
        raise PreparedBuildError(msg)
    if manifest.get("products") != product_identity(derived):
        msg = f"The prepared build {derived} changed after it was built."
        raise PreparedBuildError(msg)
    return derived


def debug_build(fallback: Path, root: Path = ROOT) -> Path:
    """The verified prepared build when CI named one, else a fresh build into `fallback`."""
    prepared = os.environ.get(PREPARED_VARIABLE)
    return verified(prepared, root) if prepared else build(fallback)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m tests.fixtures.apple_debug_build DIR")
    prepare(Path(sys.argv[1]).resolve())
