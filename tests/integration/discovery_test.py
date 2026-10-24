"""Discovery walking real package trees on disk."""

import textwrap

import pytest

from heinzel import Kernel
from heinzel.kernel import Origin, discover
from heinzel.kernel._discovery import walk

pytestmark = pytest.mark.integration


def test_discovers_builtin_modules_and_submodules():
    records, errors = discover(plugins=False)
    assert [r.qualname for r in records] == [
        "org",
        "system",
        "system.agent",
        "system.capability",
        "system.changelog",
        "system.config",
        "system.credential",
        "system.daemon",
        "system.directory",
        "system.doctor",
        "system.expert",
        "system.layer",
        "system.notify",
        "system.plugin",
        "system.runner",
        "system.theme",
        "team",
        "workflow",
    ]
    assert all(r.origin.builtin for r in records)
    assert errors == {}


def test_walks_nested_packages_and_skips_private_ones(tmp_path, monkeypatch):
    def package(path, body=""):
        path.mkdir(parents=True)
        (path / "__init__.py").write_text(textwrap.dedent(body), encoding="utf-8")

    declare = "from heinzel import Module\nmodule = Module({!r}, 'x')\n"
    root = tmp_path / "walk_root"
    package(root, declare.format("root"))
    package(root / "child", declare.format("child"))
    package(root / "child" / "grandchild", declare.format("grandchild"))
    package(root / "plain")  # no module: its children attach to the nearest declaring ancestor
    package(root / "plain" / "nested", declare.format("nested"))
    package(root / "_private", declare.format("private"))
    monkeypatch.syspath_prepend(str(tmp_path))

    import walk_root  # type: ignore[import-not-found]

    origin = Origin(distribution="walk-root", version="1", builtin=False)
    qualnames = [r.qualname for r in walk(walk_root, origin)]
    assert qualnames == ["root", "root.child", "root.child.grandchild", "root.nested"]


def test_kernel_boots_from_discovery(settings):
    records, errors = discover(plugins=False)
    kernel = Kernel(settings, records, errors)
    assert [r.qualname for r in kernel.children("system")] == [
        "system.agent",
        "system.capability",
        "system.changelog",
        "system.config",
        "system.credential",
        "system.daemon",
        "system.directory",
        "system.doctor",
        "system.expert",
        "system.layer",
        "system.notify",
        "system.plugin",
        "system.runner",
        "system.theme",
    ]
