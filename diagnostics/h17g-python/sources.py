#!/usr/bin/env python3
"""Make byte-preserving Ubuntu source variants for the one-shot diagnostic."""

import argparse
from pathlib import Path

AZURE = "http://azure.archive.ubuntu.com/ubuntu"
PRIMARY = "https://archive.ubuntu.com/ubuntu"
ALTERNATE = "https://mirrors.edge.kernel.org/ubuntu"
MIRROR_FILE = "mirror+file:/etc/apt/apt-mirrors.txt"
ALLOWED = {
    MIRROR_FILE,
    AZURE,
    PRIMARY,
    "http://archive.ubuntu.com/ubuntu",
    "http://security.ubuntu.com/ubuntu",
    "https://security.ubuntu.com/ubuntu",
}


def _validate_stanzas(source: str) -> set[str]:
    """Reject deb822 overrides and collect the original suites.

    Raises:
        ValueError: The source has unexpected trust or scope fields.
    """
    uncommented = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    stanzas = [part for part in uncommented.split("\n\n") if part.strip()]
    suites: set[str] = set()
    forbidden = {
        "trusted",
        "allow-insecure",
        "allow-weak",
        "allow-downgrade-to-insecure",
        "check-date",
        "check-valid-until",
        "valid-until-min",
        "valid-until-max",
        "date-max-future",
        "enabled",
    }
    if not stanzas:
        message = "Ubuntu source scope is empty"
        raise ValueError(message)
    for stanza in stanzas:
        fields: dict[str, str] = {}
        for line in stanza.splitlines():
            key, separator, value = line.partition(":")
            key = key.lower()
            if line[:1].isspace() or not separator or not key or key in fields or key in forbidden:
                message = "Ubuntu source has folded, duplicated or forbidden fields"
                raise ValueError(message)
            fields[key] = value.strip()
        if (
            fields.get("signed-by") != "/usr/share/keyrings/ubuntu-archive-keyring.gpg"
            or not fields.get("uris")
            or not fields.get("suites")
            or not fields.get("components")
            or "deb" not in fields.get("types", "").split()
        ):
            message = "Ubuntu archive keyring or scope is not explicitly pinned in every stanza"
            raise ValueError(message)
        suites.update(fields["suites"].split())
    return suites


def transform(source: str, mirrors: str, mirror_path: str, route: str) -> tuple[str, str]:
    """Preserve source fields while changing only the requested archive URI.

    Raises:
        ValueError: The source differs from the reviewed Ubuntu configuration.
    """
    if route not in {"unchanged", "alternate"} or AZURE not in source + mirrors:
        message = "expected stock Azure source and one predeclared route"
        raise ValueError(message)
    for line in mirrors.splitlines():
        if line.strip() and not line.lstrip().startswith("#") and line.split()[0].rstrip("/") not in ALLOWED:
            message = "Ubuntu mirror list has an unexpected endpoint"
            raise ValueError(message)
    suites = _validate_stanzas(source)
    out = []
    count = 0
    for original_line in source.splitlines(keepends=True):
        line = original_line
        key, separator, value = line.partition(":")
        if separator and key.lower() == "uris":
            items = value.split()
            if not items or any(item.rstrip("/") not in ALLOWED for item in items):
                message = "unexpected Ubuntu URI: refuse to alter source semantics"
                raise ValueError(message)
            if route == "alternate":
                items = [ALTERNATE for _ in items]
            else:
                items = [
                    f"mirror+file:{mirror_path}"
                    if x.rstrip("/") == MIRROR_FILE
                    else PRIMARY
                    if x.rstrip("/") == AZURE
                    else x
                    for x in items
                ]
            line = key + ": " + " ".join(items) + "\n"
            count += 1
        out.append(line)
    if not count or not {"noble", "noble-updates", "noble-backports", "noble-security"} <= suites:
        message = "Ubuntu Noble suite inventory is incomplete"
        raise ValueError(message)
    return "".join(out), mirrors.replace(AZURE, PRIMARY if route == "unchanged" else ALTERNATE)


def main() -> None:
    """Write the requested isolated source files."""
    parser = argparse.ArgumentParser()
    parser.add_argument("route", choices=("unchanged", "alternate"))
    parser.add_argument("source", type=Path)
    parser.add_argument("mirrors", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    mirror_path = str(args.destination / "apt-mirrors.txt")
    source, mirrors = transform(args.source.read_text(), args.mirrors.read_text(), mirror_path, args.route)
    (args.destination / "ubuntu.sources").write_text(source)
    (args.destination / "apt-mirrors.txt").write_text(mirrors)


if __name__ == "__main__":
    main()
