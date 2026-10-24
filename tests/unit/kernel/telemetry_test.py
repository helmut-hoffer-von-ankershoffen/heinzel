"""A telemetry entry is metadata only: who, when, which verb, on what; never content, secrets or addresses."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from heinzel import TeamPromises, TelemetryEntry, TelemetryLog
from heinzel.kernel import check_target

pytestmark = pytest.mark.unit

WHEN = datetime(2026, 10, 21, 13, 5, tzinfo=timezone(timedelta(hours=1)))


def entry(**fields: object) -> TelemetryEntry:
    base: dict[str, object] = {"heinzel_id": "costas-tracker", "user": "costas", "time": WHEN, "verb": "published"}
    base["target"] = "https://tracker.acme.example/boards/7"
    return TelemetryEntry.model_validate(base | fields)


def test_an_entry_is_stored_in_utc_with_its_verb_fields():
    stored = entry(verb="read", version="rev-81", needed_by=WHEN + timedelta(days=2))
    assert stored.time == datetime(2026, 10, 21, 12, 5, tzinfo=UTC)
    assert stored.needed_by == datetime(2026, 10, 23, 12, 5, tzinfo=UTC)
    assert entry(verb="approved", by="priya").by == "priya"
    assert entry(verb="fell_back", input="contract:team/tracker-update#row").input == "contract:team/tracker-update#row"
    assert entry(verb="drafted", contract_version="sha256:" + "a" * 64).contract_version.startswith("sha256:")


@pytest.mark.parametrize(
    "target",
    [
        "https://docs.acme.example/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abcdef/edit#heading=h.1",
        "team-payments@acme.example",
        "contract:team/weekly-report#report",
        "chat:#team-payments",
    ],
)
def test_targets_are_urls_addresses_contract_outputs_or_channels(target):
    assert check_target(target) == target


@pytest.mark.parametrize(
    ("target", "why"),
    [
        ("/Users/costas/drafts/row.md", "must be an https URL"),
        ("file:///tmp/row.md", "must be an https URL"),
        ("contract:me/digest#digest", "must be an https URL"),  # personal contracts are the personal layer
        ("https://costas:hunter2@tracker.acme.example/", "must be an https URL"),
        ("https://tracker.acme.example/?access_token=abc", "credential-like query parameter"),
        ("https://hooks.acme.example/xoxb-1234567890-abcdefghij", "credential"),
        ("https://tracker.acme.example/a b", "one line without spaces"),
        ("https://" + "a" * 2050 + ".example", "at most 2048"),
    ],
)
def test_other_targets_are_refused_with_what_a_target_is(target, why):
    with pytest.raises(ValueError, match=why):
        check_target(target)


@pytest.mark.parametrize(
    ("fields", "why"),
    [
        ({"description": "sent to mira@acme.example"}, "must not hold an email address"),
        ({"description": "token ghp_abcdefghijklmnopqrstuvwxyz0123"}, "looks like it holds a credential"),
        ({"description": "key AbCdEfGhIjKlMnOpQrStUvWxYz012345"}, "looks like it holds a credential"),
        ({"description": "two\nlines"}, "one line of plain text"),
        ({"description": "x" * 281}, "at most 280"),
        ({"verb": "heartbeat"}, "remove target and part"),
        ({"verb": "read", "target": None}, "needs a target"),
        ({"verb": "approved"}, "needs `by`"),
        ({"verb": "fell_back"}, "needs `input`"),
        ({"verb": "published", "by": "priya"}, "takes no by"),
        ({"verb": "drafted", "needed_by": WHEN}, "takes no needed_by"),
        ({"contract": "me:digest"}, "should match pattern"),
        ({"user": "Costas"}, "should match pattern"),
        ({"heinzel_id": "../costas"}, "should match pattern"),
        ({"time": datetime(2026, 10, 21, 12, 5)}, "timezone"),  # noqa: DTZ001 - a naive time is the point
        ({"body": "the whole report"}, "Extra inputs are not permitted"),
    ],
)
def test_what_isnt_metadata_is_refused(fields, why):
    with pytest.raises(ValidationError, match=why):
        entry(**fields)


def test_a_hex_hash_in_a_description_is_not_taken_for_a_key():
    assert entry(description="published the revision " + "ab12" * 10).description.endswith("ab12")


def test_a_heartbeat_has_no_target():
    beat = entry(verb="heartbeat", target=None, contract="team:tracker-update")
    assert (beat.target, beat.contract) == (None, "team:tracker-update")


def test_the_log_is_provided_by_a_module():
    with pytest.raises(NotImplementedError):
        TelemetryLog().entries()


def test_the_teams_promises_are_provided_by_a_module():
    with pytest.raises(NotImplementedError):
        TeamPromises().promises()
