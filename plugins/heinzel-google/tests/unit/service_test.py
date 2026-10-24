"""What the plugin says about itself, and its doctor checks, which never reach Google."""

import json

import pytest
from heinzel_google import LOGIN, GoogleService, GoogleSession, GoogleSettings, Process

from heinzel import Status

pytestmark = pytest.mark.unit


class Kernel:
    modules = ()


class Bound:
    def __init__(self, **bound: str) -> None:
        self._bound = bound

    def bound(self, capability: str) -> str | None:
        return self._bound.get(capability)


def service(session: GoogleSession) -> GoogleService:
    return GoogleService(Kernel(), session)  # type: ignore[arg-type] - only `modules` is read


def test_about_names_gws_scopes_and_apis(session, gws_path):
    about = service(session).about()
    assert (about.plugin, about.gws) == ("heinzel-google", gws_path)
    assert all(scope.endswith(".readonly") for scope in about.scopes)


def test_about_without_gws(fake_gws, tmp_path):
    missing = GoogleSession(GoogleSettings(gws_path=str(tmp_path / "nope")), fake_gws)
    assert service(missing).about().gws is None


def test_signed_in_and_bound_the_checks_are_up(session, fake_gws):
    checks = {c.name: c for c in service(session).checks(Bound(documents="google", calendar="outlook"))}
    assert checks["gws"].status == Status.UP
    assert checks["gws"].detail == "gws 0.22.5 signed in (oauth2, from encrypted_credentials); Google not contacted"
    assert checks["documents"].detail == "served by google"
    assert checks["calendar"].detail == "served by outlook, not google"
    assert "bindings" not in checks
    assert [r.arguments for r in fake_gws.runs] == [["--version"], ["auth", "status"]]


def test_without_credentials_doctor_names_the_login(session, fake_gws):
    fake_gws.status = Process(0, json.dumps({"auth_method": "none", "credential_source": "none"}).encode())
    checks = {c.name: c for c in service(session).checks(Bound())}
    assert checks["gws"].status == Status.DEGRADED
    assert checks["gws"].fix == "hz google setup"
    assert checks["gws"].detail == "gws 0.22.5 is not signed in; Google not contacted"
    assert LOGIN.startswith("gws auth login --scopes https://www.googleapis.com/auth/documents.readonly,")
    assert checks["documents"].detail.startswith("not bound")
    assert checks["bindings"].status == Status.DEGRADED


def test_without_gws_doctor_names_the_install(fake_gws, tmp_path):
    missing = GoogleSession(GoogleSettings(gws_path=str(tmp_path / "nope")), fake_gws)
    checks = {c.name: c for c in service(missing).checks(Bound(documents="google"))}
    assert checks["gws"].status == Status.DEGRADED
    assert "npm install -g @googleworkspace/cli" in checks["gws"].detail
    assert checks["gws"].fix == "hz google setup"
    assert fake_gws.runs == []


@pytest.mark.parametrize(
    "status",
    [Process(1, b"{}"), Process(0, b"not json"), Process(0, b"[]")],
    ids=["failed", "not json", "not an object"],
)
def test_an_unreadable_status_is_degraded(session, fake_gws, status):
    fake_gws.status = status
    checks = {c.name: c for c in service(session).checks(Bound())}
    assert checks["gws"].status == Status.DEGRADED
    assert "gws auth status gave no answer" in checks["gws"].detail


def test_a_status_that_cannot_start_is_degraded(session, fake_gws):
    fake_gws.error = PermissionError("denied")
    checks = {c.name: c for c in service(session).checks(Bound())}
    assert "Could not start gws: PermissionError" in checks["gws"].detail


def test_without_an_oauth_client_doctor_says_so(session, fake_gws):
    status = {"auth_method": "none", "credential_source": "none", "client_config_exists": False}
    fake_gws.status = Process(0, json.dumps(status).encode())
    checks = {c.name: c for c in service(session).checks(Bound())}
    assert checks["gws"].detail == "gws 0.22.5 has no OAuth client and is not signed in; Google not contacted"


def test_a_status_that_cannot_start_after_the_version_is_degraded(session, fake_gws):
    fake_gws.error, fake_gws.fail_after = PermissionError("denied"), 1
    checks = {c.name: c for c in service(session).checks(Bound())}
    assert "Could not start gws: PermissionError" in checks["gws"].detail
