"""Sign-in lets (ADR 0136): grants are named, explained and unique; progress is one https page; words are one line."""

import pytest
from pydantic import ValidationError

from heinzel import Grant, Module, SignInOutcome, SignInProgress, SignInStatus
from heinzel.kernel import SignInLet

pytestmark = pytest.mark.unit

READ = Grant(id="notes-read", title="Read your notes", explanation="Lets your Heinzels read notes.", default=True)
WRITE = Grant(
    id="notes-write", title="Change your notes", explanation="Lets your Heinzels edit notes.", modes=["write"]
)


class Provider:
    def status(self, *, live: bool) -> SignInStatus:
        return SignInStatus(signed_in=None, live=live)

    def sign_in(self, grants, progress, stop) -> SignInOutcome:
        return SignInOutcome(state="succeeded", message="ok")

    def sign_out(self) -> str:
        return "signed out"


def test_a_module_declares_its_sign_in_once_with_its_grants():
    module = Module("notes", "Reads Notes for tests.")
    module.sign_in(grants=[READ, WRITE])(Provider)
    [let] = [let for let in module.lets if isinstance(let, SignInLet)]
    assert (let.name, let.factory, [g.id for g in let.grants]) == ("notes", Provider, ["notes-read", "notes-write"])
    with pytest.raises(ValueError, match="already has a SignInLet"):
        module.sign_in(grants=[READ])(Provider)


def test_a_sign_in_needs_grants_and_each_name_once():
    module = Module("notes", "Reads Notes for tests.")
    with pytest.raises(ValueError, match="declares no grants"):
        module.sign_in(grants=[])(Provider)
    with pytest.raises(ValueError, match="declares a grant twice"):
        module.sign_in(grants=[READ, READ])(Provider)


@pytest.mark.parametrize("name", ["Notes", "notes write", "1notes", ""])
def test_a_grant_is_named_like_a_module(name):
    with pytest.raises(ValidationError, match="lowercase words joined by dashes"):
        Grant(id=name, title="t", explanation="e")


def test_a_grant_explains_itself_in_one_clean_line_and_orders_its_modes():
    grant = Grant(id="x", title="Change\n  notes\u202e", explanation="It edits.", modes=["write", "read"])
    assert (grant.title, grant.modes) == ("Change notes", ["read", "write"])
    with pytest.raises(ValidationError, match="title and an explanation"):
        Grant(id="x", title=" ", explanation="e")


@pytest.mark.parametrize(
    "url",
    [
        "http://accounts.example/consent",
        "https://",
        "javascript:alert(1)",
        "https://a.example/x y",
        "https://a" + "x" * 5000,
    ],
)
def test_a_consent_page_is_one_https_address(url):
    with pytest.raises(ValidationError, match="one https address"):
        SignInProgress(url=url, finish_on="host")


def test_a_consent_page_keeps_its_query_and_a_code_is_cleaned():
    progress = SignInProgress(url=" https://accounts.example/o?scope=a,b&x=1 ", code="AB\nCD", finish_on="anywhere")
    assert (progress.url, progress.code) == ("https://accounts.example/o?scope=a,b&x=1", "AB CD")


def test_an_outcome_says_what_happened_in_one_line():
    assert SignInOutcome(state="failed", message="no\nclient", account="ada\u202e").message == "no client"
    with pytest.raises(ValidationError, match="needs a message"):
        SignInOutcome(state="failed", message="  ")


def test_a_status_keeps_its_words_short():
    status = SignInStatus(signed_in=True, account="a\nb", problem="x" * 400)
    assert (status.account, len(status.problem), status.tool, status.live) == ("a b", 300, "ready", False)
