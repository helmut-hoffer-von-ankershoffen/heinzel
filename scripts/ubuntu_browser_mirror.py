# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""Configure signed Ubuntu browser dependencies with APT mirror failover."""

import os
import stat
import sys
import tempfile
from pathlib import Path

SOURCE = Path("/etc/apt/sources.list.d/ubuntu.sources")
ORIGINAL = "mirror+file:/etc/apt/apt-mirrors.txt"
KERNEL_MIRROR = "https://mirrors.edge.kernel.org/ubuntu"
MIRROR_LIST = Path("/etc/apt/heinzel-browser-mirrors.txt")
MIRROR = f"mirror+file:{MIRROR_LIST}"
MIRROR_MODE = 0o644
MIRROR_CONTENT = f"https://archive.ubuntu.com/ubuntu/\tpriority:1\n{KERNEL_MIRROR}/\tpriority:2\n"
# Some hosted images keep cloud-init's official Ubuntu archive URIs instead of the mirror list
# (actions/runner-images 9b75b60f5); the workflow's preceding step turns the Azure one into HTTPS archive.
OFFICIAL = frozenset(
    {ORIGINAL, "https://archive.ubuntu.com/ubuntu", "https://archive.ubuntu.com/ubuntu/"}
    | {
        f"http://{host}.ubuntu.com/ubuntu{slash}"
        for host in ("azure.archive", "archive", "security")
        for slash in ("", "/")
    }
)
EXPECTED = (
    {
        "Types": "deb",
        "Suites": "noble noble-updates noble-backports",
        "Components": "main universe restricted multiverse",
        "Signed-By": "/usr/share/keyrings/ubuntu-archive-keyring.gpg",
    },
    {
        "Types": "deb",
        "Suites": "noble-security",
        "Components": "main universe restricted multiverse",
        "Signed-By": "/usr/share/keyrings/ubuntu-archive-keyring.gpg",
    },
)


def _stanzas(text: str) -> list[dict[str, str]]:
    """Parse exact fields, rejecting duplicate or malformed input.

    Raises:
        ValueError: The source is malformed.
    """
    if not text.endswith("\n") or "\r" in text:
        msg = "unexpected source line endings"
        raise ValueError(msg)
    stanzas = []
    fields = {}
    for line in text.splitlines():
        if not line.strip():
            if fields:
                stanzas.append(fields)
                fields = {}
            continue
        if line.startswith("#"):
            continue
        key, separator, value = line.partition(": ")
        if not separator or key in fields:
            msg = "unexpected source field or duplicate"
            raise ValueError(msg)
        fields[key] = value
    if fields:
        stanzas.append(fields)
    return stanzas


def transform(text: str) -> str:
    """Validate the source inventory before changing any URI.

    Raises:
        ValueError: The source differs from the reviewed runner.
    """
    stanzas = _stanzas(text)
    if len(stanzas) != len(EXPECTED):
        msg = "unexpected Ubuntu source stanza inventory"
        raise ValueError(msg)
    uris = []
    for actual, expected in zip(stanzas, EXPECTED, strict=True):
        if set(actual) != {*expected, "URIs"} or any(actual[k] != v for k, v in expected.items()):
            msg = "Ubuntu source trust or scope differs from reviewed runner"
            raise ValueError(msg)
        if actual["URIs"] not in {*OFFICIAL, KERNEL_MIRROR, MIRROR}:
            msg = f"Ubuntu source URI differs from reviewed runner: {actual['URIs']!r}"
            raise ValueError(msg)
        uris.append(actual["URIs"])
    if (set(uris) & {KERNEL_MIRROR, MIRROR}) and len(set(uris)) != 1:
        msg = "mixed Ubuntu source URIs"
        raise ValueError(msg)
    replaced = {"URIs: " + uri + "\n" for uri in (*OFFICIAL, KERNEL_MIRROR)}
    return "".join("URIs: " + MIRROR + "\n" if line in replaced else line for line in text.splitlines(keepends=True))


def atomic_write(path: Path, text: str, mode: int, owner: os.stat_result) -> None:
    """Publish complete files while keeping the source owner's identity."""
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            name = file.name
            os.fchmod(file.fileno(), mode)
            os.fchown(file.fileno(), owner.st_uid, owner.st_gid)
            file.write(text)
        Path(name).replace(path)
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


def configure_mirrors(path: Path, owner: os.stat_result) -> None:
    """Leave an exact existing list intact and refuse unexpected files.

    Raises:
        ValueError: An existing list is not the reviewed regular file.
    """
    try:
        info = path.lstat()
    except FileNotFoundError:
        atomic_write(path, MIRROR_CONTENT, MIRROR_MODE, owner)
        return
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != MIRROR_MODE
        or (info.st_uid, info.st_gid) != (owner.st_uid, owner.st_gid)
        or path.read_bytes() != MIRROR_CONTENT.encode()
    ):
        msg = "unexpected browser mirror list"
        raise ValueError(msg)


def main(path: Path, mirror_list: Path) -> None:
    """Validate Ubuntu trust before configuring transport and atomically updating the source.

    Raises:
        ValueError: The source or configured mirror list differs from the reviewed runner.
    """
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        msg = "source must be a regular file"
        raise ValueError(msg)
    original = path.read_bytes().decode("utf-8")
    updated = transform(original)
    # APT can read the complete local list before any source starts referencing it.
    configure_mirrors(mirror_list, info)
    if updated != original:
        atomic_write(path, updated, stat.S_IMODE(info.st_mode), info)


if __name__ == "__main__":
    if len(sys.argv) != 1:
        msg = "This tool uses the fixed Ubuntu source path"
        raise SystemExit(msg)
    main(SOURCE, MIRROR_LIST)
