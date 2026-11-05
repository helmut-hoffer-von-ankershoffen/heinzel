"""Read bounded static package metadata from the fixture-owned tool after a failed launch."""

import os
import re
import stat
from contextlib import ExitStack, contextmanager
from itertools import islice

_INSTALLED_DISTRIBUTIONS = ("heinzel", "pydantic", "pydantic-core", "pyyaml", "fastapi", "uvicorn")
_DIAGNOSTIC_VERSION = (
    r"[0-9]{1,4}(?:\.[0-9]{1,4}){1,3}(?:(?:a|b|rc)[0-9]{1,4})?(?:\.post[0-9]{1,4})?(?:\.dev[0-9]{1,4})?"
)


@contextmanager
def _installed_directory(prefix, parts=()):
    """Refuse static prefix links, then traverse owned child directories with no-follow descriptors."""
    if not prefix.is_absolute() or prefix.resolve(strict=True) != prefix:
        raise ValueError
    with ExitStack() as stack:
        descriptor = os.open(prefix, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, descriptor)
        if os.fstat(descriptor).st_uid != os.getuid():
            raise ValueError
        for part in parts:
            if part in {"", ".", ".."} or "/" in part:
                raise ValueError
            descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            stack.callback(os.close, descriptor)
            if os.fstat(descriptor).st_uid != os.getuid():
                raise ValueError
        yield descriptor


def _installed_entries(prefix, parts):
    """Stop before traversing an unbounded installed inventory; never return discovered names to logs."""
    with _installed_directory(prefix, parts) as descriptor, os.scandir(descriptor) as entries:
        names = [entry.name for entry in islice(entries, 513)]
    if len(names) > 512:
        raise ValueError
    return names


def _installed_headers(prefix, parts):
    """Read a bounded header without following links, blocking on special files or executing installed code."""
    with _installed_directory(prefix, parts[:-1]) as directory:
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError
            content = os.read(descriptor, 16 * 1024 + 1)
        finally:
            os.close(descriptor)
    endings = [(content.find(marker), len(marker)) for marker in (b"\r\n\r\n", b"\n\n")]
    endings = [(offset, length) for offset, length in endings if offset >= 0]
    if endings:
        offset, length = min(endings)
        if offset + length > 16 * 1024:
            raise ValueError
        header = content[:offset]
    else:
        if len(content) > 16 * 1024 or info.st_size != len(content):
            raise ValueError
        header = content
    return header.decode("utf-8").splitlines()


def installed_versions(prefix):
    """Describe static installed metadata only; it is neither live-child attestation nor gate authority."""
    result = {"result": "unavailable"}
    try:
        config = _installed_headers(prefix, ("pyvenv.cfg",))
        configured = [line.removeprefix("version_info = ") for line in config if line.startswith("version_info = ")]
        implementation = [line for line in config if line.startswith("implementation = ")]
        if (
            len(configured) == 1
            and re.fullmatch(r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,3}", configured[0])
            and implementation == ["implementation = CPython"]
        ):
            result["configured_python"] = configured[0]
        libraries = [
            name for name in _installed_entries(prefix, ("lib",)) if re.fullmatch(r"python[0-9]{1,2}\.[0-9]{1,2}", name)
        ]
        if len(libraries) != 1:
            return result
        packages = ("lib", libraries[0], "site-packages")
        entries = _installed_entries(prefix, packages)
        versions = {}
        for expected in _INSTALLED_DISTRIBUTIONS:
            normalized = expected.replace("-", "_")
            candidate_name = r"[-_.]+".join(re.escape(part) for part in expected.split("-"))
            matches = [
                name for name in entries if re.fullmatch(candidate_name + r"-.*\.dist-info", name, re.IGNORECASE)
            ]
            if len(matches) != 1 or not re.fullmatch(
                re.escape(normalized) + r"-" + _DIAGNOSTIC_VERSION + r"\.dist-info", matches[0], re.IGNORECASE
            ):
                versions[expected] = "unavailable"
                continue
            headers = _installed_headers(prefix, (*packages, matches[0], "METADATA"))
            names = [line.removeprefix("Name: ") for line in headers if line.startswith("Name: ")]
            values = [line.removeprefix("Version: ") for line in headers if line.startswith("Version: ")]
            valid = len(names) == len(values) == 1 and re.sub(r"[-_.]+", "-", names[0].lower()) == expected
            valid = valid and matches[0].lower() == f"{normalized}-{values[0]}.dist-info".lower()
            versions[expected] = values[0] if valid and re.fullmatch(_DIAGNOSTIC_VERSION, values[0]) else "unavailable"
        result.update(result="static-installed-metadata", distributions=versions)
    except (OSError, ValueError, UnicodeError):
        pass
    return result
