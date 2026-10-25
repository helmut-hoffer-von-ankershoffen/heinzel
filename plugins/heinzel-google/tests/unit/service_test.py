"""What the plugin says about itself, its doctor checks, which never reach Google, and its live test."""

import json

import pytest
from heinzel_google import LOGIN, GoogleService, GoogleSession, GoogleSettings, Process

from heinzel import Login, Status

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
    assert checks["gws"].detail == "gws 0.22.5 has a sign-in (credentials.enc); checked locally, Google not contacted"
    assert checks["documents"].detail == "served by google"
    assert checks["calendar"].detail == "served by outlook, not google"
    assert "bindings" not in checks
    assert [r.arguments for r in fake_gws.runs] == [["--version"]]


def test_without_credentials_doctor_names_the_login(session, fake_gws):
    fake_gws.sign_out()
    checks = {c.name: c for c in service(session).checks(Bound())}
    assert checks["gws"].status == Status.DEGRADED
    assert checks["gws"].fix == "hz setup google"
    assert checks["gws"].detail == "gws 0.22.5 is not signed in; checked locally, Google not contacted"
    assert LOGIN.startswith("gws auth login --scopes https://www.googleapis.com/auth/documents.readonly,")
    assert checks["documents"].detail.startswith("not bound")
    assert checks["bindings"].status == Status.DEGRADED


def test_without_gws_doctor_names_the_install(fake_gws, tmp_path):
    missing = GoogleSession(GoogleSettings(gws_path=str(tmp_path / "nope")), fake_gws)
    checks = {c.name: c for c in service(missing).checks(Bound(documents="google"))}
    assert checks["gws"].status == Status.DEGRADED
    assert "npm install -g @googleworkspace/cli" in checks["gws"].detail
    assert checks["gws"].fix == "hz setup google"
    assert fake_gws.runs == []


def test_a_gws_that_cannot_start_is_degraded(session, fake_gws):
    fake_gws.error = PermissionError("denied")
    checks = {c.name: c for c in service(session).checks(Bound())}
    assert "Could not start gws: PermissionError" in checks["gws"].detail


def test_a_plain_credentials_file_counts_as_a_sign_in(session, fake_gws, gws_config):
    fake_gws.sign_out()
    (gws_config / "credentials.json").write_text("{}", encoding="utf-8")
    checks = {c.name: c for c in service(session).checks(Bound())}
    assert checks["gws"].detail.startswith("gws 0.22.5 has a sign-in (credentials.json)")


def test_without_an_oauth_client_doctor_says_so(session, fake_gws):
    fake_gws.sign_out(client=False)
    checks = {c.name: c for c in service(session).checks(Bound())}
    assert (
        checks["gws"].detail
        == "gws 0.22.5 has no OAuth client and is not signed in; checked locally, Google not contacted"
    )


def test_the_gws_check_says_whether_gws_is_signed_in_and_how_to_sign_in(session, fake_gws, tmp_path):
    gws = {c.name: c for c in service(session).checks(Bound())}["gws"]
    assert gws.login == Login(signed_in=True, command="hz setup google")
    fake_gws.sign_out()
    gws = {c.name: c for c in service(session).checks(Bound())}["gws"]
    assert gws.login == Login(signed_in=False, command="hz setup google")
    missing = GoogleSession(GoogleSettings(gws_path=str(tmp_path / "nope")), fake_gws)
    assert {c.name: c for c in service(missing).checks(Bound())}["gws"].login.signed_in is False


def _answer(fake_gws, body, exit_code=0):
    fake_gws.on(("drive", "about", "get"), Process(exit_code, json.dumps(body).encode(), b""))


def test_the_live_test_asks_drive_who_is_signed_in_with_a_short_timeout(session, fake_gws):
    _answer(fake_gws, {"user": {"displayName": "Ada", "emailAddress": "ada@example.com"}})
    (check,) = service(session).live()
    assert check.status is Status.UP
    assert check.login == Login(signed_in=True, account="ada@example.com", site="google.com", command="hz setup google")
    (run,) = fake_gws.api_runs
    assert run.command == ("drive", "about", "get")
    assert run.params == {"fields": "user(displayName,emailAddress)"}
    assert run.timeout == 15.0


def test_a_live_test_whose_sign_in_expired_is_not_signed_in(session, fake_gws):
    _answer(fake_gws, {"error": {"code": 401, "message": "Invalid Credentials"}}, exit_code=1)
    (check,) = service(session).live()
    assert check.status is Status.DEGRADED
    assert check.fix == "hz setup google"
    assert check.login == Login(signed_in=False, command="hz setup google")


def test_a_live_test_google_refuses_otherwise_is_down(session, fake_gws):
    _answer(fake_gws, {"error": {"code": 500, "message": "Backend Error"}}, exit_code=1)
    (check,) = service(session).live()
    assert check.status is Status.DOWN
    assert check.login is None


def test_a_live_answer_without_a_user_names_no_account(session, fake_gws):
    _answer(fake_gws, {})
    (check,) = service(session).live()
    assert check.status is Status.UP
    assert check.login.account == ""
