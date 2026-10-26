"""A string-list change preserves other origins and ordinary writers under one configuration lock."""

import os
import threading
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from heinzel import Config, ConfigEditor, CurrentUser, HeinzelError
from heinzel.modules.system.config import _service

pytestmark = pytest.mark.integration
ORIGIN = "https://workstation.tail-example.ts.net"
OTHER = "https://other.example.test"


@pytest.fixture
def editor(make_kernel):
    return OwnerEditor(make_kernel(builtins=True))


class OwnerEditor:
    def __init__(self, kernel):
        self.user = kernel.resolve(CurrentUser)
        self.editor = kernel.resolve(ConfigEditor)

    def write(self, *arguments, **keywords):
        with self.user.local("cli"):
            return self.editor.write(*arguments, **keywords)

    def change_list(self, *arguments, **keywords):
        with self.user.local("cli"):
            return self.editor.change_list(*arguments, **keywords)


def test_merge_and_remove_preserve_existing_origins_and_unrelated_values(editor):
    editor.write("hz.log_level", "ERROR")
    editor.write("hz.ui_allowed_origins", [OTHER])
    added = editor.change_list("hz.ui_allowed_origins", add=(ORIGIN,))
    assert set(added.values) == {OTHER, ORIGIN}
    assert added.added == {ORIGIN}
    assert added.removed == frozenset()
    removed = editor.change_list("hz.ui_allowed_origins", remove=(ORIGIN,))
    assert removed.values == (OTHER,)
    assert removed.removed == {ORIGIN}
    assert removed.added == frozenset()
    text = (Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml").read_text()
    assert tomllib.loads(text)["hz"] == {"log_level": "ERROR", "ui_allowed_origins": [OTHER]}


def test_higher_priority_environment_origin_policy_is_not_silently_rewritten(editor, monkeypatch):
    monkeypatch.setenv("HZ_UI_ALLOWED_ORIGINS", OTHER)
    with pytest.raises(HeinzelError, match="controlled by env"):
        editor.change_list("hz.ui_allowed_origins", add=(ORIGIN,))
    assert not (Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml").exists()


def test_non_list_setting_is_refused_without_writing(editor):
    with pytest.raises(HeinzelError, match="plain list of strings"):
        editor.change_list("hz.log_level", add=("ERROR",))
    assert not (Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml").exists()


def test_concurrent_list_changes_preserve_both_origins(make_kernel, tmp_path):
    editor = OwnerEditor(make_kernel(builtins=True, config=Config.load(tmp_path)))
    start = threading.Barrier(3)

    def add(origin):
        start.wait(timeout=5)
        editor.change_list("hz.ui_allowed_origins", add=(origin,))

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(add, ORIGIN)
        two = pool.submit(add, OTHER)
        start.wait(timeout=5)
        one.result(timeout=5)
        two.result(timeout=5)
    path = Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml"
    assert set(tomllib.loads(path.read_text())["hz"]["ui_allowed_origins"]) == {ORIGIN, OTHER}


def test_ordinary_write_does_not_replace_a_list_change_from_an_older_file_snapshot(editor, monkeypatch):
    first_saving = threading.Event()
    release_first = threading.Event()
    second_attempted = threading.Event()
    second_saved = threading.Event()
    save = _service._save

    def pause_first(path, document):
        if threading.current_thread().name == "first-writer":
            first_saving.set()
            assert release_first.wait(timeout=5)
        save(path, document)
        if threading.current_thread().name == "second-writer":
            second_saved.set()

    monkeypatch.setattr(_service, "_save", pause_first)

    def first():
        threading.current_thread().name = "first-writer"
        editor.change_list("hz.ui_allowed_origins", add=(ORIGIN,))

    def second():
        threading.current_thread().name = "second-writer"
        second_attempted.set()
        editor.write("hz.log_level", "ERROR")

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(first)
        assert first_saving.wait(timeout=5)
        two = pool.submit(second)
        assert second_attempted.wait(timeout=5)
        second_saved.wait(timeout=0.1)  # Let an unlocked writer reach its stale save; the real lock holds it back.
        release_first.set()
        one.result(timeout=5)
        two.result(timeout=5)
    path = Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml"
    assert tomllib.loads(path.read_text())["hz"] == {"ui_allowed_origins": [ORIGIN], "log_level": "ERROR"}


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "public"])
def test_unsafe_existing_lock_is_refused_without_touching_config(editor, tmp_path, kind):
    directory = Path(os.environ["HZ_CONFIG_HOME"])
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".config.toml.lock"
    original = tmp_path / "untouched"
    original.write_text("original")
    original.chmod(0o600)
    if kind == "symlink":
        lock.symlink_to(original)
    elif kind == "hardlink":
        lock.hardlink_to(original)
    else:
        lock.write_text("")
        lock.chmod(0o644)
    with pytest.raises(HeinzelError, match="lock"):
        editor.change_list("hz.ui_allowed_origins", add=(ORIGIN,))
    assert original.read_text() == "original"
    assert not (directory / "config.toml").exists()


def test_new_configuration_and_lock_start_private_even_with_permissive_umask(editor):
    previous = os.umask(0)
    try:
        path = editor.write("hz.log_level", "ERROR")
    finally:
        os.umask(previous)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.with_name(".config.toml.lock").stat().st_mode & 0o777 == 0o600


def test_preexisting_predictable_temporary_symlink_is_not_followed_or_deleted(editor, tmp_path):
    directory = Path(os.environ["HZ_CONFIG_HOME"])
    directory.mkdir(parents=True, exist_ok=True)
    victim = tmp_path / "untouched.txt"
    victim.write_text("original")
    unowned = directory / ".config.toml.tmp"
    unowned.symlink_to(victim)
    path = editor.write("hz.log_level", "ERROR")
    assert victim.read_text() == "original"
    assert unowned.is_symlink()
    assert not path.is_symlink()
