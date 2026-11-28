"""Verify and remove only the artifact overrides from an installed uv receipt."""

import tomllib
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any, cast

import tomlkit
from packaging.utils import canonicalize_name

from heinzel.spec._errors import HeinzelError

OVERRIDE_ERROR = "The uv receipt overrides do not match the verified artifacts; inspect the installation manually."


def _expected_overrides(overrides: Mapping[str, Path]) -> dict[str, str]:
    expected: dict[str, str] = {}
    for name, path in overrides.items():
        normalized = canonicalize_name(name, validate=True)
        if normalized in expected or not path.is_absolute() or ".." in path.parts:
            raise HeinzelError(OVERRIDE_ERROR)
        expected[normalized] = str(path)
    if not expected or len(set(expected.values())) != len(expected):
        raise HeinzelError(OVERRIDE_ERROR)
    return expected


def _receipt_overrides(entries: object) -> dict[str, str]:
    if not isinstance(entries, list):
        raise HeinzelError(OVERRIDE_ERROR)
    actual: dict[str, str] = {}
    for entry in cast("list[object]", entries):
        if not isinstance(entry, dict):
            raise HeinzelError(OVERRIDE_ERROR)
        values = cast("dict[str, Any]", entry)
        if (
            set(values) != {"name", "path"}
            or not isinstance(values["name"], str)
            or not isinstance(values["path"], str)
        ):
            raise HeinzelError(OVERRIDE_ERROR)
        normalized = canonicalize_name(values["name"], validate=True)
        if normalized in actual:
            raise HeinzelError(OVERRIDE_ERROR)
        actual[normalized] = values["path"]
    return actual


def without_verified_overrides(raw: bytes, overrides: Mapping[str, Path]) -> bytes:
    """Remove only an exact set of named file overrides, preserving every other parsed receipt value.

    Raises:
        HeinzelError: If the receipt or the expected overrides cannot be verified.
    """
    try:
        expected = _expected_overrides(overrides)
        original = tomllib.loads(raw.decode("utf-8"))
        tool = original["tool"]
        actual = _receipt_overrides(tool["overrides"])
        document = tomlkit.parse(raw.decode("utf-8"))
        table = cast("MutableMapping[str, Any]", document["tool"])
        del table["overrides"]
        replacement = tomlkit.dumps(document).encode("utf-8")
        del tool["overrides"]
        preserved = tomllib.loads(replacement.decode("utf-8")) == original
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise HeinzelError(OVERRIDE_ERROR) from exc
    if actual != expected or not preserved:
        raise HeinzelError(OVERRIDE_ERROR)
    return replacement


without_verified_overrides.__module__ = "heinzel.kernel.install._receipt_overrides"
