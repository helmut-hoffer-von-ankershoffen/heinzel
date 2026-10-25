"""Private API credentials bind an owner to one expiring server audience without leaking the bearer."""

import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from heinzel import HeinzelError, Principal
from heinzel.heads.api._auth import ApiBearer

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)
OWNER = Principal("u-" + "a" * 32)


def test_bearer_verifies_exact_secret_expiry_and_server_audience():
    token = ApiBearer.issue(OWNER, "server-a", now=NOW)
    header = f"Bearer {token.secret}"
    identity = token.authenticate(header, "server-a", now=NOW)
    assert identity is not None
    assert identity.principal == OWNER
    assert identity.authentication == "api-bearer"
    assert identity.permissions == frozenset({"private:read", "private:write"})
    assert token.authenticate(header, "server-b", now=NOW) is None
    assert token.authenticate(header, "server-a", now=token.expires_at) is None
    assert token.authenticate(header, "server-a", now=NOW + timedelta(days=1)) is None
    for invalid in (token.secret, f"bearer {token.secret}", "Bearer wrong", ""):
        assert token.authenticate(invalid, "server-a", now=NOW) is None
    assert token.secret not in repr(token)


def test_client_copy_is_private_and_never_replaces_an_existing_file(tmp_path):
    token = ApiBearer.issue(OWNER, "server-a", now=NOW)
    path = tmp_path / "api" / "credential.json"
    token.write(path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert json.loads(path.read_text()) == {
        "audience": token.audience,
        "credential_id": token.credential_id,
        "expires_at": token.expires_at.isoformat(),
        "token": token.secret,
    }
    before = path.read_bytes()
    with pytest.raises(HeinzelError):
        ApiBearer.issue(OWNER, "other", now=NOW).write(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("unsafe", ["file-link", "folder-link", "public-folder"])
def test_credential_refuses_links_and_public_directories_without_private_error_paths(tmp_path, unsafe):
    token = ApiBearer.issue(OWNER, "server-a", now=NOW)
    folder = tmp_path / "private-place"
    folder.mkdir(mode=0o700)
    path = folder / "credential.json"
    if unsafe == "file-link":
        path.symlink_to(tmp_path / "target")
    elif unsafe == "folder-link":
        original = tmp_path / "original"
        folder.rename(original)
        folder.symlink_to(original, target_is_directory=True)
    else:
        folder.chmod(0o755)
    with pytest.raises(HeinzelError) as caught:
        token.write(path)
    assert str(tmp_path) not in str(caught.value)
    assert token.secret not in str(caught.value)


def test_interrupted_credential_publication_leaves_no_partial_client_copy(tmp_path, monkeypatch):
    token = ApiBearer.issue(OWNER, "server-a", now=NOW)
    path = tmp_path / "api" / "credential.json"

    def interrupted(*args, **kwargs):
        msg = "simulated publication failure"
        raise OSError(msg)

    monkeypatch.setattr(os, "link", interrupted)
    with pytest.raises(HeinzelError):
        token.write(path)
    assert not path.exists()
    assert not list(path.parent.glob(".credential-*"))
