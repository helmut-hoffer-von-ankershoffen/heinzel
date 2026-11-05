"""Failed readiness may describe bounded installed metadata without running the installed SDK."""

import os
import subprocess
from types import SimpleNamespace

import pytest

from tests.e2e import _ui_service_metadata as _ui_service
from tests.e2e._ui_service_metadata import installed_versions

pytestmark = pytest.mark.unit


@pytest.fixture
def installed(tmp_path):
    prefix = tmp_path.resolve() / "sdk"
    packages = prefix / "lib/python3.14/site-packages"
    packages.mkdir(parents=True)
    (prefix / "pyvenv.cfg").write_text(
        "home = /private/secret-owner/python\nimplementation = CPython\nversion_info = 3.14.10\n"
    )
    versions = {
        "heinzel": "0.9.17",
        "pydantic": "2.14.3",
        "pydantic-core": "2.50.4",
        "pyyaml": "6.0.3",
        "fastapi": "0.162.0",
        "uvicorn": "0.54.0",
    }
    for name, version in versions.items():
        directory = packages / f"{name.replace('-', '_')}-{version}.dist-info"
        directory.mkdir()
        (directory / "METADATA").write_text(
            f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n"
            "Author: private-owner-secret\n\n/private/body #token=private-token\nVersion: 999.999\n"
        )
    return prefix, packages, versions


def test_installed_diagnostics_report_static_versions_without_importing_or_executing(installed, monkeypatch):
    prefix, packages, versions = installed
    (packages / "sitecustomize.py").write_text("raise RuntimeError('Installed code must never execute')\n")
    (packages / "private-customer-9.9.9.dist-info").mkdir()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("No interpreter command"))
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("No installed process"))
    result = installed_versions(prefix)
    assert result == {
        "result": "static-installed-metadata",
        "configured_python": "3.14.10",
        "distributions": versions,
    }
    assert not any(secret in repr(result) for secret in ("private", "token", "Author", "home", "sitecustomize"))
    assert "runtime_python" not in result


@pytest.mark.parametrize(
    "content",
    [
        "Name: private-customer\nVersion: 2.14.3\n",
        "Name: pydantic\nVersion: private-secret\n",
        "Name: pydantic\nVersion: 2.14.3+private-secret\n",
        "Name: pydantic\nVersion: 2.14.3\nVersion: 2.14.3\n",
        "Name: pydantic\nName: pydantic\nVersion: 2.14.3\n",
        "Name: pydantic\nVersion: 2.14.4\n",
        "Name: pydantic\nVersion: 12345.14.3\n",
    ],
)
def test_distribution_metadata_never_selects_expected_identity_or_leaks_unknown_values(installed, content):
    prefix, packages, _versions = installed
    (packages / "pydantic-2.14.3.dist-info/METADATA").write_text(content)
    result = installed_versions(prefix)
    assert result["distributions"]["pydantic"] == "unavailable"
    assert "private" not in repr(result)


@pytest.mark.parametrize("version", ["2.14.4", "2.14.4+private-local", "private-secret", ""])
def test_duplicate_distribution_is_ambiguous_instead_of_choosing_a_version(installed, version):
    prefix, packages, _versions = installed
    duplicate = packages / f"pydantic-{version}.dist-info"
    duplicate.mkdir()
    (duplicate / "METADATA").write_text("Name: pydantic\nVersion: 2.14.4\n")
    assert installed_versions(prefix)["distributions"]["pydantic"] == "unavailable"


def test_normalized_distribution_alias_cannot_hide_an_ambiguous_second_installation(installed):
    prefix, packages, _versions = installed
    (packages / "pydantic.core-2.50.5.dist-info").mkdir()
    assert installed_versions(prefix)["distributions"]["pydantic-core"] == "unavailable"


def test_large_metadata_body_is_ignored_after_a_complete_bounded_crlf_header(installed, monkeypatch):
    prefix, packages, versions = installed
    (packages / "pydantic-2.14.3.dist-info/METADATA").write_bytes(
        b"Name: pydantic\r\nVersion: 2.14.3\r\n\r\n" + b"private-secret" * 100_000
    )
    original = os.read
    reads = []

    def read(descriptor, size):
        reads.append(size)
        return original(descriptor, size)

    monkeypatch.setattr(os, "read", read)
    assert installed_versions(prefix)["distributions"] == versions
    assert reads == [16 * 1024 + 1] * 7


@pytest.mark.parametrize("kind", ["crlf", "truncated-version"])
def test_raw_header_bound_never_accepts_normalized_or_truncated_metadata(installed, kind):
    prefix, packages, _versions = installed
    if kind == "crlf":
        content = b"Name: pydantic\r\nVersion: 2.14.3\r\n" + b"X: private\r\n" * 2000
    else:
        ending = b"\r\nVersion: 2.14.3"
        start = b"Name: pydantic\r\nX: "
        content = start + b"x" * (16 * 1024 + 1 - len(start) - len(ending)) + ending + b"9999\r\n"
    (packages / "pydantic-2.14.3.dist-info/METADATA").write_bytes(content)
    assert installed_versions(prefix)["result"] == "unavailable"


@pytest.mark.parametrize(
    "content",
    [
        "implementation = CPython\nversion_info = private-secret\n",
        "implementation = private-secret\nversion_info = 3.14.10\n",
        "implementation = CPython\nversion_info = 3.14.10\nversion_info = 3.14.11\n",
        "implementation = CPython\nversion_info = 3.14.10 /private/secret\n",
    ],
)
def test_configured_interpreter_version_is_bounded_and_not_inferred_from_a_path(installed, content):
    prefix, _packages, versions = installed
    (prefix / "pyvenv.cfg").write_text(content)
    result = installed_versions(prefix)
    assert "configured_python" not in result
    assert result["distributions"] == versions
    assert "private" not in repr(result)


@pytest.mark.parametrize(
    "part",
    [
        "pyvenv.cfg",
        "lib",
        "lib/python3.14",
        "lib/python3.14/site-packages",
        "lib/python3.14/site-packages/pydantic-2.14.3.dist-info",
        "lib/python3.14/site-packages/pydantic-2.14.3.dist-info/METADATA",
    ],
)
def test_installed_metadata_refuses_symlink_files_and_parent_directories(installed, part):
    prefix, _packages, _versions = installed
    path = prefix / part
    target = path.with_name(path.name + "-original")
    path.rename(target)
    path.symlink_to(target, target_is_directory=target.is_dir())
    assert installed_versions(prefix)["result"] == "unavailable"


def test_installed_metadata_refuses_a_linked_prefix(installed):
    prefix, _packages, _versions = installed
    link = prefix.with_name("linked-sdk")
    link.symlink_to(prefix, target_is_directory=True)
    assert installed_versions(link) == {"result": "unavailable"}


@pytest.mark.parametrize("part", ["pyvenv.cfg", "lib/python3.14/site-packages/pydantic-2.14.3.dist-info/METADATA"])
def test_special_metadata_files_are_nonblocking_and_never_read(installed, monkeypatch, part):
    prefix, _packages, _versions = installed
    path = prefix / part
    path.unlink()
    os.mkfifo(path)
    original = os.read

    def read(descriptor, size):
        assert not _ui_service.stat.S_ISFIFO(os.fstat(descriptor).st_mode)
        return original(descriptor, size)

    monkeypatch.setattr(os, "read", read)
    assert installed_versions(prefix)["result"] == "unavailable"


@pytest.mark.parametrize("kind", ["header", "directory", "multiple-libraries", "utf8", "missing"])
def test_unavailable_or_oversized_inventory_does_not_replace_the_original_failure(installed, kind):
    prefix, packages, _versions = installed
    if kind == "header":
        (prefix / "pyvenv.cfg").write_text("x" * (16 * 1024 + 1))
    elif kind == "directory":
        for index in range(513):
            (packages / f"private-{index}").touch()
    elif kind == "multiple-libraries":
        (prefix / "lib/python3.15").mkdir()
    elif kind == "utf8":
        (prefix / "pyvenv.cfg").write_bytes(b"\xff")
    else:
        (prefix / "pyvenv.cfg").unlink()
    assert installed_versions(prefix)["result"] == "unavailable"


def test_installed_metadata_refuses_foreign_file_ownership(installed, monkeypatch):
    prefix, _packages, _versions = installed
    original = os.fstat

    def foreign(descriptor):
        info = original(descriptor)
        return SimpleNamespace(st_uid=info.st_uid + 1, st_mode=info.st_mode)

    monkeypatch.setattr(os, "fstat", foreign)
    assert installed_versions(prefix) == {"result": "unavailable"}
