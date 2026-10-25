"""Trusted installation identity survives independent processes and refuses unsafe storage."""

import os

import pytest

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal

pytestmark = pytest.mark.unit


def test_context_is_required_deny_by_default_and_restored_on_failure(tmp_path):
    user = CurrentUser(tmp_path / "identity")
    identity = CallIdentity(Principal("u-" + "1" * 32), "api-bearer", "test", "server")
    assert identity.permissions == frozenset()
    with pytest.raises(HeinzelError, match="authenticated"):
        user.principal()
    with user.using(identity):
        assert user.principal() == identity.principal

        def fails():
            with user.using(None):
                with pytest.raises(HeinzelError, match="authenticated"):
                    user.principal()
                raise RuntimeError

        with pytest.raises(RuntimeError):
            fails()
        assert user.context() is identity
    with pytest.raises(HeinzelError, match="authenticated"):
        user.context()
    with user.local("cli"):
        assert user.context().permissions == frozenset({"private:read", "private:write"})


@pytest.mark.parametrize("target", ["directory", "owner", "lock", "hardlink", "permissions", "malformed"])
def test_unsafe_or_damaged_storage_is_refused_without_private_path(tmp_path, target):
    secret = tmp_path / "private-location"
    directory = secret / "identity"
    user = CurrentUser(directory)
    user.owner()
    owner = directory / "owner-v1"
    if target == "directory":
        moved = secret / "moved"
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    elif target in {"owner", "lock"}:
        path = owner if target == "owner" else directory / "owner.lock"
        moved = secret / "moved"
        path.rename(moved)
        path.symlink_to(moved)
    elif target == "hardlink":
        os.link(owner, secret / "copy")
    elif target == "permissions":
        owner.chmod(0o644)
    else:
        owner.write_text("invalid\n")
    with pytest.raises(HeinzelError) as caught:
        user.owner()
    assert str(secret) not in str(caught.value)
    assert caught.value.__cause__ is None


def test_a_crash_before_publish_cannot_leave_a_partial_owner(tmp_path, monkeypatch):
    directory = tmp_path / "identity"
    original = os.rename

    def interrupt(*args, **kwargs):
        msg = "simulated failure before publication"
        raise OSError(msg)

    monkeypatch.setattr(os, "rename", interrupt)
    with pytest.raises(HeinzelError):
        CurrentUser(directory).owner()
    assert not (directory / "owner-v1").exists()
    assert not list(directory.glob(".owner-*"))
    monkeypatch.setattr(os, "rename", original)
    assert CurrentUser(directory).owner() == CurrentUser(directory).owner()


def test_scopes_are_copied_before_a_trusted_caller_can_change_them():
    scopes = {"private:read"}
    identity = CallIdentity(Principal("u-" + "1" * 32), "api-bearer", "test", "server", scopes)
    scopes.add("private:write")
    assert identity.permissions == frozenset({"private:read"})
