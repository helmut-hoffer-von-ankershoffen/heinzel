from pathlib import Path

import pytest

from heinzel import Config
from heinzel.kernel import FILENAME, ConfigFile, user_config_dir
from heinzel.modules.system.config import ConfigService
from heinzel.modules.system.config._reveal import Revealer, launch

pytestmark = pytest.mark.unit

DESKTOP = {"DISPLAY": ":0"}


class FakeOpener:
    def __init__(self, error: OSError | None = None) -> None:
        self.commands: list[list[str]] = []
        self.error = error

    def __call__(self, command):
        self.commands.append(list(command))
        if self.error:
            raise self.error


def which(name: str) -> str | None:
    return f"/usr/bin/{name}"


def revealer(platform: str, environ=None, opener=None, found=which) -> Revealer:
    return Revealer(platform, found, environ or {}, opener or FakeOpener())


@pytest.fixture
def config_file(tmp_path) -> Path:
    path = tmp_path / "heinzel" / "config.toml"
    path.parent.mkdir()
    path.write_text("", encoding="utf-8")
    return path


def test_macos_selects_an_existing_file_in_finder(config_file):
    opener = FakeOpener()
    done = revealer("darwin", opener=opener).reveal(config_file)
    assert opener.commands == [["/usr/bin/open", "-R", str(config_file)]]
    assert (done.supported, done.opened, done.message) == (True, True, f"Showed {config_file} in Finder.")


def test_macos_opens_the_nearest_existing_folder_of_a_missing_file_and_creates_nothing(tmp_path):
    missing = tmp_path / "not" / "yet" / "config.toml"
    opener = FakeOpener()
    done = revealer("darwin", opener=opener).reveal(missing)
    assert opener.commands == [["/usr/bin/open", str(tmp_path)]]
    assert done.opened
    assert str(missing) in done.message
    assert not (tmp_path / "not").exists()


def test_windows_selects_an_existing_file_in_explorer(config_file):
    opener = FakeOpener()
    rev = revealer("win32", opener=opener)
    done = rev.reveal(config_file)
    assert opener.commands == [["/usr/bin/explorer", f"/select,{config_file}"]]
    assert (rev.file_manager, done.opened) == ("Explorer", True)


def test_windows_opens_the_folder_of_a_missing_file(config_file):
    opener = FakeOpener()
    revealer("win32", opener=opener).reveal(config_file.with_name("other.toml"))
    assert opener.commands == [["/usr/bin/explorer", str(config_file.parent)]]


def test_linux_with_a_desktop_opens_the_folder_with_xdg_open(config_file):
    opener = FakeOpener()
    rev = revealer("linux", DESKTOP, opener)
    done = rev.reveal(config_file)
    assert opener.commands == [["/usr/bin/xdg-open", str(config_file.parent)]]
    assert (rev.file_manager, done.supported, done.opened) == ("file manager", True, True)


def test_linux_with_wayland_counts_as_a_desktop(tmp_path):
    opener = FakeOpener()
    revealer("linux", {"WAYLAND_DISPLAY": "wayland-0"}, opener).reveal(tmp_path / "missing" / "config.toml")
    assert opener.commands == [["/usr/bin/xdg-open", str(tmp_path)]]


def test_linux_without_a_display_opens_nothing_and_names_the_file(config_file):
    opener = FakeOpener()
    done = revealer("linux", {}, opener).reveal(config_file)
    assert opener.commands == []
    assert (done.supported, done.opened) == (False, False)
    assert str(config_file) in done.message
    assert "desktop session" in done.message


def test_a_missing_opener_is_unsupported(config_file):
    done = revealer("darwin", found=lambda name: None).reveal(config_file)
    assert (done.supported, done.opened) == (False, False)
    assert "open isn't installed" in done.message


def test_an_opener_that_fails_to_start_says_so(config_file):
    done = revealer("darwin", opener=FakeOpener(OSError(13, "Permission denied"))).reveal(config_file)
    assert (done.supported, done.opened) == (True, False)
    assert "Permission denied" in done.message


def test_launch_starts_the_command_detached(monkeypatch):
    started = []
    monkeypatch.setattr("subprocess.Popen", lambda command, **options: started.append((command, options)))
    launch(("/usr/bin/open", "-R", "config.toml"))
    command, options = started[0]
    assert command == ["/usr/bin/open", "-R", "config.toml"]
    assert options["start_new_session"] is True


def service(make_kernel, files, rev: Revealer) -> ConfigService:
    kernel = make_kernel(config=Config(files=tuple(files), data={}))
    config = ConfigService(kernel)
    config.revealer = rev
    return config


def test_reveal_finds_the_file_of_a_scope(make_kernel, config_file):
    opener = FakeOpener()
    config = service(make_kernel, [ConfigFile("site", config_file)], revealer("darwin", opener=opener))
    done = config.reveal("site")
    assert (done.scope, done.path, done.opened) == ("site", config_file, True)
    assert opener.commands == [["/usr/bin/open", "-R", str(config_file)]]


def test_the_user_file_is_known_even_when_no_file_was_read(make_kernel):
    opener = FakeOpener()
    done = service(make_kernel, [], revealer("darwin", opener=opener)).reveal("user")
    assert done.path == user_config_dir() / FILENAME
    assert done.opened


def test_without_a_project_nothing_opens_and_it_says_how_to_create_one(make_kernel):
    opener = FakeOpener()
    done = service(make_kernel, [], revealer("darwin", opener=opener)).reveal("project")
    assert (done.path, done.supported, done.opened) == (None, True, False)
    assert "--scope project" in done.message
    assert opener.commands == []


def test_without_a_site_file_nothing_opens(make_kernel):
    done = service(make_kernel, [], revealer("linux")).reveal("site")
    assert (done.path, done.supported, done.opened) == (None, False, False)


def test_the_report_says_whether_and_how_a_file_can_be_revealed(make_kernel):
    assert service(make_kernel, [], revealer("darwin")).report().model_dump(include={"can_reveal", "file_manager"}) == {
        "can_reveal": True,
        "file_manager": "Finder",
    }
    linux = service(make_kernel, [], revealer("linux")).report()
    assert (linux.can_reveal, linux.file_manager) == (False, "file manager")
