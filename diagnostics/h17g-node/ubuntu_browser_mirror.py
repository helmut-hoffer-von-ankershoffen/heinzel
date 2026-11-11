"""Select the reviewed browser-dependency mirror without changing Ubuntu trust."""

import os
import stat
import sys
import tempfile
from pathlib import Path

SOURCE = Path("/etc/apt/sources.list.d/ubuntu.sources")
ORIGINAL = "mirror+file:/etc/apt/apt-mirrors.txt"
MIRROR = "https://mirrors.edge.kernel.org/ubuntu"
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
        if actual["URIs"] not in {ORIGINAL, MIRROR}:
            msg = "Ubuntu source URI differs from reviewed runner"
            raise ValueError(msg)
        uris.append(actual["URIs"])
    if len(set(uris)) != 1:
        msg = "mixed Ubuntu source URIs"
        raise ValueError(msg)
    return "".join(
        "URIs: " + MIRROR + "\n" if line == "URIs: " + ORIGINAL + "\n" else line
        for line in text.splitlines(keepends=True)
    )


def main(path: Path) -> None:
    """Atomically replace only a validated regular source file.

    Raises:
        ValueError: The source is not a regular file.
    """
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        msg = "source must be a regular file"
        raise ValueError(msg)
    original = path.read_bytes().decode("utf-8")
    updated = transform(original)
    if updated == original:
        return
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            name = file.name
            os.fchmod(file.fileno(), stat.S_IMODE(info.st_mode))
            os.fchown(file.fileno(), info.st_uid, info.st_gid)
            file.write(updated)
        Path(name).replace(path)
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


if __name__ == "__main__":
    if len(sys.argv) != 1:
        msg = "This tool uses the fixed Ubuntu source path"
        raise SystemExit(msg)
    main(SOURCE)
