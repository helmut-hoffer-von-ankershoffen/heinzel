"""`hz setup google`: finding gws, signing it in, binding the capabilities; with a scripted person and a fake gws."""

from pathlib import Path

import pytest
from heinzel_google import (
    CALENDAR_WRITE_SCOPE,
    LOGIN,
    LOGIN_WITH_MAIL,
    MAIL_SCOPE,
    MAIL_WRITE_SCOPE,
    GoogleSession,
    GoogleSettings,
    login_command,
)
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
    """Configured bindings, and those Google serves automatically as the only installed adapter (ADR 0055)."""

    def __init__(self, automatic: tuple[str, ...] = (), **bound: str) -> None:
        self._bound = bound | dict.fromkeys(automatic, "google")
        self._automatic = automatic

    def bound(self, capability: str) -> str | None:
        return self._bound.get(capability)

    def binding(self, capability: str) -> tuple[str | None, str]:
        bound = self._bound.get(capability)
        if capability in self._automatic:
            return bound, "automatic"
        return bound, "configured" if bound else "unbound"


def setup(session, fake_gws, answers, view=None, settings=None) -> tuple[ScriptedPrompter, Editor]:
    prompter, editor = ScriptedPrompter(answers), Editor()
    run_setup(settings or GoogleSettings(), session, fake_gws, view or Bound(), prompter, editor)  # type: ignore[arg-type]
    return prompter, editor


def test_signs_in_and_binds_documents_and_calendar_but_not_mail_by_default(session, fake_gws, gws_path):
    fake_gws.sign_out()
    prompter, editor = setup(session, fake_gws, ["", "", "", "", ""])
    assert fake_gws.logins == [LOGIN.split()[1:]]
    assert {r.executable for r in fake_gws.runs} == {gws_path}
    assert editor.written == {"capabilities.documents": "google", "capabilities.calendar": "google"}
    assert "gws is signed in." in prompter.transcript


def test_binding_mail_signs_in_again_with_gmails_scope(session, fake_gws):
    prompter, editor = setup(session, fake_gws, ["y", "", "", ""], Bound(documents="google", calendar="google"))
    assert editor.written == {"capabilities.mail": "google"}
    assert fake_gws.logins == [LOGIN_WITH_MAIL.split()[1:]]
    assert any("all of your mail" in line for line in prompter.transcript)


def test_already_signed_in_and_bound_runs_no_sign_in(session, fake_gws):
    _, editor = setup(session, fake_gws, ["n", "n"], Bound(documents="google", calendar="google", mail="other"))
    assert (fake_gws.logins, editor.written) == ([], {})


def test_keeps_another_binding_when_declined_and_reports_a_failed_login(session, fake_gws):
    fake_gws.sign_out()
    fake_gws.login_code = 1
    prompter, editor = setup(session, fake_gws, ["n", "", "n", "n", "y"], Bound(documents="other"))
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
        ["relative/gws", str(tmp_path), gws_path, "n", "n"],
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
    _, editor = setup(session, fake_gws, ["n", "n", "n"], Bound(documents="google", calendar="google"))
    assert (fake_gws.logins, editor.written) == ([], {})


def test_mail_bound_before_offers_signing_in_again_with_gmails_scope(session, fake_gws):
    bound = Bound(documents="google", calendar="google", mail="google")
    _, editor = setup(session, fake_gws, ["", "", ""], bound)
    assert (fake_gws.logins, editor.written) == ([], {})
    setup(session, fake_gws, ["", "", "y"], bound)
    assert fake_gws.logins == [LOGIN_WITH_MAIL.split()[1:]]


def test_google_serving_automatically_is_asked_and_declined_mail_is_turned_off(session, fake_gws):
    prompter, editor = setup(session, fake_gws, ["", "", "", ""], Bound(automatic=("documents", "calendar", "mail")))
    assert editor.written == {
        "capabilities.documents": "google",
        "capabilities.calendar": "google",
        "capabilities.mail": "none",
    }
    assert not any("instead of google" in line for line in prompter.transcript)
    assert fake_gws.logins == []  # signed in already, and mail stays off


def test_letting_hz_write_the_calendar_signs_in_again_with_its_write_scope_only(session, fake_gws):
    prompter, _ = setup(session, fake_gws, ["n", "y", ""], Bound(documents="google", calendar="google"))
    (login,) = fake_gws.logins
    scopes = login[login.index("--scopes") + 1].split(",")
    assert CALENDAR_WRITE_SCOPE in scopes
    assert MAIL_WRITE_SCOPE not in scopes
    assert MAIL_SCOPE not in scopes
    assert any("change your calendar once you confirm each change" in line for line in prompter.transcript)


def test_letting_hz_write_mail_asks_for_gmails_modify_scope_instead_of_its_read_only_one(session, fake_gws):
    bound = Bound(documents="google", calendar="google", mail="google")
    setup(session, fake_gws, ["n", "y", ""], bound)
    assert fake_gws.logins == [login_command(mail=True, write=frozenset({"mail"})).split()[1:]]
    (login,) = fake_gws.logins
    scopes = login[login.index("--scopes") + 1].split(",")
    assert MAIL_WRITE_SCOPE in scopes
    assert MAIL_SCOPE not in scopes


def test_writes_are_offered_only_for_what_google_serves(session, fake_gws):
    prompter, _ = setup(session, fake_gws, ["n", "n"], Bound(documents="google", calendar="other", mail="other"))
    assert not any("Let hz also" in line for line in prompter.transcript)
