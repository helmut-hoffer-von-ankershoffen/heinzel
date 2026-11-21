"""`hz system update` asks the target release's Python requirement before it installs (`UpdateService`)."""

import sys

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation, ReleaseSource
from heinzel.modules.system._service import GitHubReleases
from heinzel.modules.system.updater._update import UpdateService

pytestmark = pytest.mark.unit

MAJOR, MINOR = sys.version_info[:2]


class Releases(ReleaseSource):
    def __init__(self, requires: str | None = None, error: Exception | None = None) -> None:
        self.requires, self.error, self.asked = requires, error, []

    def requires_python(self, version, timeout):
        self.asked.append(version)
        if self.error:
            raise self.error
        return self.requires


def service(releases):
    return UpdateService(Installation(Progress(), uv="uv"), releases)


def test_a_release_for_this_python_keeps_the_tool_interpreter():
    releases = Releases(f">={MAJOR}.{MINOR}")
    assert service(releases).needed_python("9.9.9") is None
    assert releases.asked == ["9.9.9"]


def test_a_release_for_a_newer_python_names_it():
    assert service(Releases(f">={MAJOR}.{MINOR + 1}")).needed_python("9.9.9") == f"{MAJOR}.{MINOR + 1}"


def test_an_unknown_requirement_keeps_the_tool_interpreter():
    assert service(Releases(error=OSError("offline"))).needed_python("9.9.9") is None
    assert service(Releases(None)).needed_python("9.9.9") is None
    assert service(None).needed_python("9.9.9") is None
    assert service(Releases(">=4")).needed_python(None) is None


def test_a_requirement_no_python_meets_is_refused():
    with pytest.raises(HeinzelError, match="keeps working"):
        service(Releases(">=4")).needed_python("9.9.9")


def test_the_base_release_source_cannot_tell():
    assert ReleaseSource().requires_python("1.0.0", 1.0) is None


def test_github_releases_read_requires_python_from_pypi(monkeypatch):
    from heinzel.modules.system import _service

    urls = []

    def fetch(url, timeout):
        urls.append(url)
        return {"info": {"requires_python": ">=3.15"}}

    monkeypatch.setattr(_service, "fetch", fetch)
    assert GitHubReleases("owner/name").requires_python("0.9.44", 1.0) == ">=3.15"
    assert urls == ["https://pypi.org/pypi/heinzel/0.9.44/json"]
    monkeypatch.setattr(_service, "fetch", lambda url, timeout: {"info": {"requires_python": ""}})
    assert GitHubReleases("owner/name").requires_python("0.9.44", 1.0) is None
    monkeypatch.setattr(_service, "fetch", lambda url, timeout: [])
    assert GitHubReleases("owner/name").requires_python("0.9.44", 1.0) is None


def test_the_doctor_reports_the_running_python_and_what_the_last_update_found(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from heinzel.kernel._tool_python import PythonNeed
    from heinzel.modules.system import doctor

    settings = SimpleNamespace(cache_dir=tmp_path)
    version = sys.version.split()[0]
    monkeypatch.setattr(doctor, "managed_root", lambda executable: None)
    assert doctor.python_check(settings, tool=True).detail == version  # nothing recorded: just the version
    monkeypatch.setattr(doctor, "managed_root", lambda executable: tmp_path / "cpython-3.15.1-macos-aarch64-none")
    assert (
        doctor.python_check(settings, tool=True).detail == f"{version}, uv-managed (cpython-3.15.1-macos-aarch64-none)"
    )
    assert doctor.python_check(settings, tool=False).detail == version
    need = PythonNeed(tmp_path)
    need.remember("9.9.9", f"{MAJOR}.{MINOR + 1}")
    check = doctor.python_check(settings, tool=True)
    assert check.status.value == "up"
    assert f"hz 9.9.9 needs Python {MAJOR}.{MINOR + 1}: the next update installs it onto a uv-managed" in check.detail
    need.remember("9.9.9", f"{MAJOR}.{MINOR}")  # moved already: no hint
    assert "needs" not in doctor.python_check(settings, tool=True).detail
    need.remember("9.9.9", f"{MAJOR}.{MINOR + 1}", held="uv could not provide a uv-managed Python")
    check = doctor.python_check(settings, tool=True)
    assert check.status.value == "degraded"
    assert "its update is held: uv could not provide" in check.detail
    assert check.fix == f"uv python install {MAJOR}.{MINOR + 1}, then: hz system update"
    need.remember("9.9.9", "", held="no newer Python satisfies it")
    assert doctor.python_check(settings, tool=True).fix == "hz system update"
    need.forget()
    need.forget()  # nothing left to forget is fine
    assert need.read() is None


@pytest.mark.parametrize("body", ["not json", "[]", '{"release": 1, "python": "3.15"}', '{"release": "1"}'])
def test_a_damaged_need_record_reads_as_none(tmp_path, body):
    from heinzel.kernel._tool_python import NEED_FILENAME, PythonNeed

    (tmp_path / NEED_FILENAME).write_text(body)
    assert PythonNeed(tmp_path).read() is None


def test_an_unwritable_need_record_only_loses_the_hint(tmp_path):
    from heinzel.kernel._tool_python import PythonNeed

    blocker = tmp_path / "file"
    blocker.write_text("")
    PythonNeed(blocker / "cache").remember("1", "3.15")  # no error
    assert PythonNeed(blocker / "cache").read() is None


def test_a_managed_python_is_named_by_its_uv_folder(tmp_path, monkeypatch):
    from heinzel.kernel._tool_python import managed_root

    managed = tmp_path / "uv" / "python" / "cpython-3.15.1-macos-aarch64-none" / "bin" / "python3.15"
    managed.parent.mkdir(parents=True)
    managed.write_text("")
    assert managed_root(managed) == managed.parents[1].resolve()
    elsewhere = tmp_path / "custom" / "cpython-3.15.1-x" / "bin" / "python3.15"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text("")
    monkeypatch.delenv("UV_PYTHON_INSTALL_DIR", raising=False)
    assert managed_root(elsewhere) is None
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(tmp_path / "custom"))
    assert managed_root(elsewhere) == elsewhere.parents[1].resolve()
    assert managed_root(tmp_path / "missing") is None


class FakeInstallation:
    """Only what `_interpreter_for` uses: a newer interpreter that is had, or not."""

    def __init__(self, error: HeinzelError | None = None) -> None:
        self.error, self.entered = error, []

    def newer_interpreter(self, version, environment=None):
        from contextlib import contextmanager

        @contextmanager
        def scope():
            if self.error is not None:
                raise self.error
            self.entered.append(version)
            yield f"/managed/python{version}"

        return scope()


def interpreter_service(tmp_path, releases, installation):
    from heinzel.kernel._tool_python import PythonNeed

    need = PythonNeed(tmp_path)
    return UpdateService(installation, releases, need=need), need


def test_the_last_update_records_what_it_found(tmp_path):
    newer = f"{MAJOR}.{MINOR + 1}"
    installation = FakeInstallation()
    updates, need = interpreter_service(tmp_path, Releases(f">={newer}"), installation)
    with updates._interpreter_for("9.9.9"):
        assert installation.entered == [newer]
    assert need.read() == {"release": "9.9.9", "python": newer, "held": None}
    updates, need = interpreter_service(tmp_path, Releases(f">={MAJOR}.{MINOR}"), installation)
    with updates._interpreter_for("9.9.9"):
        pass
    assert need.read() is None


def test_an_update_held_for_lack_of_the_newer_python_is_recorded(tmp_path):
    newer = f"{MAJOR}.{MINOR + 1}"
    installation = FakeInstallation(HeinzelError("uv could not provide it"))
    updates, need = interpreter_service(tmp_path, Releases(f">={newer}"), installation)
    with pytest.raises(HeinzelError, match="could not provide"), updates._interpreter_for("9.9.9"):
        pytest.fail("entered without the interpreter")
    assert need.read() == {"release": "9.9.9", "python": newer, "held": "uv could not provide it"}
    updates, need = interpreter_service(tmp_path, Releases(">=4"), installation)
    with pytest.raises(HeinzelError, match="keeps working"), updates._interpreter_for("9.9.9"):
        pytest.fail("entered for an impossible requirement")
    held = need.read()
    assert held is not None
    assert held["python"] == ""
    assert "keeps working" in (held["held"] or "")


def test_a_failure_inside_the_update_is_not_recorded_as_a_hold(tmp_path):
    newer = f"{MAJOR}.{MINOR + 1}"
    updates, need = interpreter_service(tmp_path, Releases(f">={newer}"), FakeInstallation())
    failed = HeinzelError("uv failed")
    with pytest.raises(HeinzelError, match="uv failed"), updates._interpreter_for("9.9.9"):
        raise failed
    assert need.read() == {"release": "9.9.9", "python": newer, "held": None}


class Highest(Releases):
    def __init__(self, highest: str | Exception) -> None:
        super().__init__()
        self.highest = highest

    def highest_version(self, timeout, *, prereleases=False):
        if isinstance(self.highest, Exception):
            raise self.highest
        return self.highest


def pin(kind, git=None):
    from types import SimpleNamespace

    return SimpleNamespace(kind=kind, git=git, requirement="heinzel")


def test_the_release_an_install_moves_to_is_the_one_asked():
    updates = service(Highest("0.9.44"))
    assert updates._target_release(pin("release", "g"), "0.9.43", "0.9.44", "v0.9.44") == "0.9.44"
    assert updates._target_release(pin("release", "g"), "0.9.43", None, "v0.9.45") == "0.9.45"
    assert updates._target_release(pin("ref", "g"), "0.9.43", None, "git+https://example.test/x@main") is None
    assert updates._target_release(pin("index"), "0.9.43", "0.9.44", None) == "0.9.44"  # uv resolves the newest
    assert updates._target_release(pin("default"), "0.9.44", "0.9.44", None) is None  # nothing newer to move to
    assert updates._target_release(pin("default", "g"), "0.9.43", "0.9.44", None) is None  # a branch: unknown
    assert service(Highest(OSError("offline")))._target_release(pin("index"), "0.9.43", None, None) is None


class Recreated:
    """A tool uv recreated for another interpreter: a new generation, the same content (or not)."""

    def __init__(self, content: str, *, changes: bool = True) -> None:
        self.generation, self.content, self.changes_interpreter = "old", content, changes
        self.recorded: list[str] = []

    def startup_fingerprint(self):
        return self.generation

    def content_fingerprint(self):
        return self.content

    def startup_state_matches(self):
        return True

    def record_startup_state(self, *, expected):
        self.recorded.append(expected)


@pytest.mark.parametrize(("after", "same"), [("same files", True), ("other files", False)])
def test_a_restore_into_a_recreated_folder_counts_by_content_and_renews_the_record(after, same):
    from heinzel.modules.system.updater._update_rules import ORIGINALS

    tool = Recreated("same files")
    updates = UpdateService(tool, None)  # type: ignore[arg-type] - only the generation seams are used
    token = ORIGINALS.set({})
    try:
        original = updates._original_generation()
        tool.generation, tool.content = "new folder", after
        assert updates._same_generation(original) is same
    finally:
        ORIGINALS.reset(token)
    assert tool.recorded == (["new folder"] if same else [])


def test_without_a_move_only_the_exact_generation_counts():
    tool = Recreated("same files", changes=False)
    updates = UpdateService(tool, None)  # type: ignore[arg-type] - only the generation seams are used
    original = updates._original_generation()
    assert updates._same_generation(original)
    tool.generation = "new folder"
    assert not updates._same_generation(original)
    assert not updates._same_generation(None)
    assert tool.recorded == []
