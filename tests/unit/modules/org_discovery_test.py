"""Finding who you are through gh, gws, the system clock and PATH, with the CLIs faked."""

import json
import subprocess

import pytest

from heinzel.modules.org import _discovery as discovery
from heinzel.modules.org._discovery import Login

pytestmark = pytest.mark.unit

# The autouse fixture in tests/conftest.py replaces `discover`; the real one is kept here to test it.
DISCOVER = discovery.discover
RUN = discovery._run


def person(email="mira@example.com", name="Mira Example", *, primary=True) -> str:
    return json.dumps(
        {
            "resourceName": "people/me",
            "names": [{"displayName": "Old Name"}, {"metadata": {"primary": primary}, "displayName": name}],
            "emailAddresses": [{"metadata": {"primary": primary}, "value": email}],
        }
    )


@pytest.fixture
def cli(monkeypatch):
    """Fake CLIs: `cli({"gh api user": (0, "{...}")})`; unlisted commands fail, `None` executables are missing."""

    def install(answers: dict[str, tuple[int, str] | Exception], *, missing: tuple[str, ...] = ()):
        calls: list[list[str]] = []
        monkeypatch.setattr(discovery, "_run", RUN)
        monkeypatch.setattr(discovery.shutil, "which", lambda name: None if name in missing else f"/usr/bin/{name}")

        def run(args, **kwargs):
            assert kwargs["stdin"] is subprocess.DEVNULL  # a CLI never reads the wizard's terminal
            calls.append(args)
            command = " ".join([args[0].rsplit("/", 1)[-1], *args[1:3]])
            answer = answers.get(command, (1, ""))
            if isinstance(answer, Exception):
                raise answer
            return subprocess.CompletedProcess(args, answer[0], stdout=answer[1], stderr="")

        monkeypatch.setattr(discovery.subprocess, "run", run)
        return calls

    return install


def test_the_github_login_and_name_come_from_gh_api_user(cli):
    cli({"gh api user": (0, json.dumps({"login": "Mira-Example", "name": " Mira "}))})
    assert discovery.github_login() == Login(id="Mira-Example", name="Mira")


def test_without_the_api_the_login_gh_stored_is_read_offline(cli):
    calls = cli({"gh config get": (0, "Mira-Example\n")})
    assert discovery.github_login() == Login(id="Mira-Example")
    assert calls[-1] == ["/usr/bin/gh", "config", "get", "--host", "github.com", "user"]


@pytest.mark.parametrize(
    "answers",
    [
        {},
        {"gh api user": (0, "not json"), "gh config get": (0, "")},
        {"gh api user": (0, '{"login": "not a login!"}'), "gh config get": (0, "not a login!")},
        {"gh api user": subprocess.TimeoutExpired("gh", 10), "gh config get": OSError("broken")},
    ],
)
def test_no_github_login_when_gh_cant_say(cli, answers):
    cli(answers)
    assert discovery.github_login() is None


def test_no_github_login_without_gh(cli):
    cli({}, missing=("gh",))
    assert discovery.github_login() is None


def test_the_google_login_is_the_primary_email_and_name_of_your_people_profile(cli):
    calls = cli({"gws people people": (0, person())})
    assert discovery.google_login() == Login(id="mira@example.com", name="Mira Example")
    assert json.loads(calls[0][-1]) == {"resourceName": "people/me", "personFields": "names,emailAddresses"}


def test_without_a_primary_entry_the_first_is_taken(cli):
    cli({"gws people people": (0, person(primary=False))})
    assert discovery.google_login() == Login(id="mira@example.com", name="Old Name")


@pytest.mark.parametrize(
    "answer",
    [(1, '{"error": {"code": 401}}'), (0, "[]"), (0, person(email="nobody")), (0, '{"emailAddresses": []}')],
)
def test_no_google_login_when_gws_is_signed_out_or_answers_oddly(cli, answer):
    cli({"gws people people": answer})
    assert discovery.google_login() is None


def test_no_google_login_without_gws(cli):
    cli({}, missing=("gws",))
    assert discovery.google_login() is None


def test_the_local_time_zone_is_where_localtime_points(monkeypatch, tmp_path):
    zone = tmp_path / "zoneinfo" / "Europe" / "Berlin"
    zone.parent.mkdir(parents=True)
    zone.write_text("", encoding="utf-8")
    link = tmp_path / "localtime"
    link.symlink_to(zone)
    monkeypatch.setattr(discovery, "LOCALTIME", link)
    assert discovery.local_timezone() == "Europe/Berlin"


def test_no_local_time_zone_without_a_zoneinfo_link(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "LOCALTIME", tmp_path / "missing")
    assert discovery.local_timezone() is None
    plain = tmp_path / "localtime"
    plain.write_text("", encoding="utf-8")
    monkeypatch.setattr(discovery, "LOCALTIME", plain)
    assert discovery.local_timezone() is None


def test_discover_says_where_each_part_came_from(monkeypatch):
    monkeypatch.setattr(discovery, "github_login", lambda: Login(id="mira"))
    monkeypatch.setattr(discovery, "google_login", lambda: None)
    monkeypatch.setattr(discovery, "local_timezone", lambda: "Europe/Berlin")
    monkeypatch.setattr(discovery, "claude_installed", lambda: True)
    found = DISCOVER()
    assert found.coding_agent == "claude-code"
    assert found.sources == {
        "github": "your gh login",
        "timezone": "this machine's clock",
        "coding_agent": "claude on your PATH",
    }


def test_claude_code_is_found_on_path(monkeypatch):
    monkeypatch.setattr(discovery.shutil, "which", lambda name: "/usr/local/bin/claude" if name == "claude" else None)
    assert discovery.claude_installed()


def test_display_names_are_stored_printable_and_short():
    assert discovery.clean_name(" Mira\x1b[31m Example\n") == "Mira[31m Example"
    assert discovery.clean_name("x" * 300) == "x" * 200
    assert discovery.clean_name("\x00") is None
    assert discovery.clean_name(None) is None
