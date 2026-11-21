"""The E2E managed interpreter copy remains inside its private installation."""

import pytest

from tests.e2e.conftest import copy_managed_installation

pytestmark = pytest.mark.unit


def test_private_copy_remaps_absolute_and_relative_internal_links(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "library").write_text("fixture")
    (source / "absolute").symlink_to(source / "library")
    (source / "relative").symlink_to("library")
    copied = tmp_path / "private"

    copy_managed_installation(source, copied)

    for name in ("absolute", "relative"):
        assert (copied / name).resolve(strict=True) == copied / "library"
        assert (copied / name).read_text() == "fixture"


def test_private_copy_rejects_escaping_link_before_copying(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (tmp_path / "outside").write_text("outside")
    (source / "escape").symlink_to("../outside")
    copied = tmp_path / "private"

    with pytest.raises(ValueError, match="link outside"):
        copy_managed_installation(source, copied)
    assert not copied.exists()


def test_managed_runner_is_reused_with_exact_uv_selection(tmp_path, monkeypatch):
    from subprocess import CompletedProcess

    from tests.e2e.conftest import managed_source

    directory = tmp_path / "pythons"
    interpreter = directory / "cpython-fixture/bin/python3.15"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    calls = []

    def uv(args, *, env, **options):
        calls.append(args[4:])
        assert "VIRTUAL_ENV" not in env
        assert "UV_PYTHON" not in env
        assert env["UV_NO_CONFIG"] == "1"
        assert options["check"]
        assert options["timeout"] == 10
        return CompletedProcess(args, 0, f"{directory if args[4] == 'dir' else interpreter}\n")

    monkeypatch.setattr("tests.e2e.conftest.subprocess.run", uv)
    assert (
        managed_source("3.15.2", interpreter, tmp_path, {"UV_PYTHON": "other", "VIRTUAL_ENV": "other"}) == interpreter
    )
    assert calls == [["dir"], ["find", "--no-project", "--managed-python", "--no-python-downloads", "3.15.2"]]
    assert not (tmp_path / "fixture-python-source").exists()


@pytest.fixture
def system_runner(tmp_path, monkeypatch):
    from subprocess import CompletedProcess
    from types import SimpleNamespace

    runner = tmp_path / "homebrew/bin/python3.15"
    runner.parent.mkdir(parents=True)
    runner.touch()
    absent = tmp_path / "uv-python-dir"
    shared = tmp_path / "pytest-run"
    shared.mkdir()
    probe = SimpleNamespace(runner=runner, absent=absent, shared=shared, installs=[], failure=None, barrier=None)
    seed = shared / "fixture-python-source"
    directory = seed / "home/pythons"
    interpreter = directory / "cpython-fixture/bin/python3.15"
    probe.interpreter = interpreter

    def uv(args, *, env, **options):
        action = args[4]
        assert options["check"]
        if action == "dir":
            if probe.barrier is not None:
                probe.barrier.wait(timeout=2)
            return CompletedProcess(args, 0, f"{absent}\n")
        assert env["HOME"] == str(seed / "home")
        assert env["UV_PYTHON_INSTALL_DIR"] == str(directory)
        assert env["UV_CACHE_DIR"] == str(seed / "cache")
        assert env["UV_NO_CONFIG"] == "1"
        assert "UV_PYTHON" not in env
        if action == "install":
            probe.installs.append(args)
            assert args[-4:] == ["--no-bin", "--install-dir", str(directory), "3.15.2"]
            assert options["timeout"] == 60
            if probe.failure is not None:
                raise probe.failure
            interpreter.parent.mkdir(parents=True)
            interpreter.touch()
            return CompletedProcess(args, 0)
        assert args[4:] == ["find", "--no-project", "--managed-python", "--no-python-downloads", "3.15.2"]
        assert options["timeout"] == 10
        return CompletedProcess(args, 0, f"{interpreter}\n")

    monkeypatch.setattr("tests.e2e.conftest.subprocess.run", uv)
    return probe


def test_system_runner_without_managed_directory_provisions_shared_exact_patch_once(system_runner):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from tests.e2e.conftest import managed_source

    system_runner.barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = [
            workers.submit(managed_source, "3.15.2", system_runner.runner, system_runner.shared, {"UV_PYTHON": "other"})
            for _ in range(2)
        ]
        assert first.result(timeout=2) == second.result(timeout=2) == system_runner.interpreter
    assert len(system_runner.installs) == 1
    assert not system_runner.absent.exists()


@pytest.mark.parametrize("timed_out", [False, True])
def test_shared_managed_provisioning_failure_stops_other_workers(system_runner, timed_out):
    from subprocess import CalledProcessError, TimeoutExpired

    from tests.e2e.conftest import managed_source

    failure = TimeoutExpired(["uv"], 60) if timed_out else CalledProcessError(1, ["uv"])
    system_runner.failure = failure
    args = ("3.15.2", system_runner.runner, system_runner.shared, {})
    with pytest.raises(type(failure)):
        managed_source(*args)
    with pytest.raises(pytest.fail.Exception, match="already failed"):
        managed_source(*args)
    assert len(system_runner.installs) == 1
    assert not system_runner.interpreter.exists()
