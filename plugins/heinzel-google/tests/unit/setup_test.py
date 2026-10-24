"""`hz setup google`: finding gws, signing it in, binding the capabilities; with a scripted person and a fake gws."""

from pathlib import Path

import pytest
from heinzel_google import LOGIN, LOGIN_WITH_MAIL, GoogleSession, GoogleSettings
from heinzel_google._setup import run_setup

from heinzel.kernel import ScriptedPrompter

pytestmark = pytest.mark.unit


class Editor:
    def __init__(self) -> None:
        self.written: dict[str, object] = {}

    def write(self, key: str, value: object, scope: str = "user") -> Path:
        self.written[key] = value
        return Path("/home/ada/.config/hz/config.toml")


class Bound:
    def __init__(self, **bound: str) -> None:
        self._bound = bound

    def bound(self, capability: str) -> str | None:
        return self._bound.get(capability)


def setup(session, fake_gws, answers, view=None, settings=None) -> tuple[ScriptedPrompter, Editor]:
    prompter, editor = ScriptedPrompter(answers), Editor()
    run_setup(settings or GoogleSettings(), session, fake_gws, view or Bound(), prompter, editor)  # type: ignore[arg-type]
    return prompter, editor


def test_signs_in_and_binds_documents_and_calendar_but_not_mail_by_default(session, fake_gws, gws_path):
    fake_gws.sign_out()
    prompter, editor = setup(session, fake_gws, ["", "", "", ""])
    assert fake_gws.logins == [LOGIN.split()[1:]]
    assert {r.executable for r in fake_gws.runs} == {gws_path}
    assert editor.written == {"capabilities.documents": "google", "capabilities.calendar": "google"}
    assert "gws is signed in." in prompter.transcript


def test_binding_mail_signs_in_again_with_gmails_scope(session, fake_gws):
    prompter, editor = setup(session, fake_gws, ["y", ""], Bound(documents="google", calendar="google"))
    assert editor.written == {"capabilities.mail": "google"}
    assert fake_gws.logins == [LOGIN_WITH_MAIL.split()[1:]]
    assert any("all of your mail" in line for line in prompter.transcript)


def test_already_signed_in_and_bound_asks_nothing(session, fake_gws):
    _, editor = setup(session, fake_gws, ["n"], Bound(documents="google", calendar="google", mail="other"))
    assert (fake_gws.logins, editor.written) == ([], {})


def test_keeps_another_binding_when_declined_and_reports_a_failed_login(session, fake_gws):
    fake_gws.sign_out()
    fake_gws.login_code = 1
    prompter, editor = setup(session, fake_gws, ["n", "", "n", "y"], Bound(documents="other"))
    assert editor.written == {"capabilities.calendar": "google"}
    assert "gws auth login did not finish (exit 1)." in prompter.transcript


def test_without_an_oauth_client_it_stops_with_the_way_to_make_one(session, fake_gws):
    fake_gws.sign_out(client=False)
    prompter, editor = setup(session, fake_gws, [])
    assert any("gws auth setup" in line for line in prompter.transcript)
    assert (fake_gws.logins, editor.written) == ([], {})


def test_asks_for_the_path_of_gws_when_it_is_not_found(fake_gws, gws_path, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    session = GoogleSession(GoogleSettings(), fake_gws)
    prompter, editor = setup(
        session,
        fake_gws,
        ["relative/gws", str(tmp_path), gws_path, "n"],
        Bound(documents="google", calendar="google", mail="other"),
    )
    assert editor.written == {"google.gws_path": gws_path}
    assert "Please give an absolute path, such as /opt/homebrew/bin/gws." in prompter.transcript
    assert f"{tmp_path} is not an executable file." in prompter.transcript
    assert {r.executable for r in fake_gws.runs} == {gws_path}


def test_without_gws_and_no_path_it_names_the_install(fake_gws, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    prompter, editor = setup(GoogleSession(GoogleSettings(), fake_gws), fake_gws, [""])
    assert any("npm install -g @googleworkspace/cli" in line for line in prompter.transcript)
    assert editor.written == {}


def test_a_declined_sign_in_runs_nothing(session, fake_gws):
    fake_gws.sign_out()
    _, editor = setup(session, fake_gws, ["n", "n"], Bound(documents="google", calendar="google"))
    assert (fake_gws.logins, editor.written) == ([], {})


def test_mail_bound_before_offers_signing_in_again_with_gmails_scope(session, fake_gws):
    bound = Bound(documents="google", calendar="google", mail="google")
    _, editor = setup(session, fake_gws, [""], bound)
    assert (fake_gws.logins, editor.written) == ([], {})
    setup(session, fake_gws, ["y"], bound)
    assert fake_gws.logins == [LOGIN_WITH_MAIL.split()[1:]]
