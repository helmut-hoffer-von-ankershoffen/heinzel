"""Who someone is, and drafting a roster from a GitHub team (`team._roster`)."""

import pytest

from heinzel.kernel import HeinzelError
from heinzel.modules.team._repo import CommandFailed
from heinzel.modules.team._roster import MAX_ROSTER, Identity, check_source, draft_roster, identities, roster_command

pytestmark = pytest.mark.unit


def answering(output: str | Exception):
    def run(command, cwd):
        if isinstance(output, Exception):
            raise output
        return output

    return run


def test_the_github_login_gh_is_signed_in_as_is_who_you_are():
    assert identities(answering("Mira-Example\n")) == [Identity(source="github", login="Mira-Example")]


@pytest.mark.parametrize(
    "answer",
    [CommandFailed(["gh"], "not signed in"), HeinzelError("gh isn't installed"), "not a login!\n"],
)
def test_no_identity_when_gh_is_missing_signed_out_or_strange(answer):
    assert identities(answering(answer)) == []


@pytest.mark.parametrize(
    ("source", "path"),
    [
        ("github:acme-example/payments", "orgs/acme-example/teams/payments/members"),
        ("github:acme-example", "orgs/acme-example/members"),
    ],
)
def test_a_github_team_or_organisation_is_read_with_gh_api(source, path):
    assert roster_command(source) == ["gh", "api", "--paginate", path, "--jq", ".[].login"]


@pytest.mark.parametrize("source", ["github:", "gitlab:acme", "github:acme/../x", "github:-acme", "acme"])
def test_other_sources_are_refused(source):
    with pytest.raises(ValueError, match="isn't a roster source"):
        check_source(source)


def test_the_roster_keeps_githubs_order_and_each_login_once():
    assert draft_roster(answering("theo\n\nMira\nmira\n"), "github:acme") == ["theo", "Mira"]


def test_a_roster_larger_than_a_team_is_refused():
    logins = "\n".join(f"person-{n}" for n in range(MAX_ROSTER + 1))
    with pytest.raises(ValueError, match="more than a team's"):
        draft_roster(answering(logins), "github:acme")
