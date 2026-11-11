#!/usr/bin/env python3
"""Check the single authenticated APT download record and the downloaded bytes."""

import argparse
import hashlib
import hmac
import os
import re
import stat
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

# apt-private/private-download.cc DoDownload prints URI, basename, size, HashSum.
RECORD = re.compile(
    r"'([^'\s]+)' (libnss3_[A-Za-z0-9.+~%:_-]+_amd64\.deb) ([1-9][0-9]*) (SHA256|SHA512):([0-9a-fA-F]+)\n?\Z"
)


def parse(path: Path, mirror_list: Path) -> tuple[str, int, str, str]:
    """Read exactly one strong-hash APT URI record.

    Raises:
        ValueError: The record is malformed or does not identify a strong hash.
    """
    match = RECORD.fullmatch(path.read_text(encoding="utf-8"))
    if match is None:
        message = "expected exactly one well-formed libnss3:amd64 URI record"
        raise ValueError(message)
    uri, name, size, algorithm, digest = match.groups()
    parts = urlsplit(uri)
    https = parts.scheme == "https" and parts.hostname and not parts.username and not parts.password
    prefix = f"mirror+file:{mirror_list}/"
    suffix = uri.removeprefix(prefix)
    decoded = unquote(suffix)
    mirror = (
        uri.startswith(prefix)
        and re.fullmatch(r"pool/[A-Za-z0-9.+~%_/-]+\.deb", suffix) is not None
        and re.search(r"%(?![0-9a-fA-F]{2})", suffix) is None
        and decoded.count("/") == suffix.count("/")
        and ".." not in decoded.split("/")
    )
    if not https and not mirror:
        message = "invalid package URI"
        raise ValueError(message)
    if len(digest) != {"SHA256": 64, "SHA512": 128}[algorithm]:
        message = "invalid digest length"
        raise ValueError(message)
    return name, int(size), algorithm, digest.lower()


def verify(record: Path, mirror_list: Path, package: Path) -> None:
    """Check the emitted name, size and digest against one regular nonsymlink file.

    Raises:
        ValueError: The name, file type, size or digest does not match.
    """
    name, size, algorithm, expected = parse(record, mirror_list)
    if package.name != name:
        message = "downloaded package filename mismatch"
        raise ValueError(message)
    fd = os.open(package, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != size:
            message = "downloaded package is not a regular file of the expected size"
            raise ValueError(message)
        actual = hashlib.file_digest(stream, algorithm.lower()).hexdigest()
    if not hmac.compare_digest(actual, expected):
        message = "downloaded package digest mismatch"
        raise ValueError(message)
    print(f"PACKAGE_BYTES_VERIFIED=1 ALGORITHM={algorithm} SIZE={size} DIGEST={actual}")  # noqa: T201


def main() -> int:
    """Parse a record or verify a downloaded package against it."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    parse_cmd = sub.add_parser("parse")
    parse_cmd.add_argument("record", type=Path)
    parse_cmd.add_argument("mirror_list", type=Path)
    verify_cmd = sub.add_parser("verify")
    verify_cmd.add_argument("record", type=Path)
    verify_cmd.add_argument("mirror_list", type=Path)
    verify_cmd.add_argument("package", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "parse":
            name, size, algorithm, digest = parse(args.record, args.mirror_list)
            print(f"PACKAGE_RECORD_VALID=1 NAME={name} SIZE={size} ALGORITHM={algorithm} DIGEST={digest}")  # noqa: T201
        else:
            verify(args.record, args.mirror_list, args.package)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"PACKAGE_PROOF_FAILED={exc}", file=sys.stderr)  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
