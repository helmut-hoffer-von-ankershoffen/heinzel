"""Capability specs and requirement strings (ADR 0028)."""

import inspect
from datetime import datetime
from typing import get_type_hints

import pytest
from pydantic import ValidationError

from heinzel.kernel import CAPABILITIES, Requirement
from heinzel.kernel._capabilities import capability_of, parse_requirements
from heinzel.kernel._protocols import (
    Calendar,
    Chat,
    Documents,
    Event,
    Mail,
    MailContent,
    MailMessage,
    MailQuery,
    Meeting,
    Meetings,
    Message,
    Participant,
    PostedMessage,
    Tracker,
    Transcript,
    TranscriptSegment,
    Window,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", sorted(CAPABILITIES))
def test_every_protocol_method_has_a_mode_and_a_docstring(name):
    spec = CAPABILITIES[name]
    methods = {n for n, _ in inspect.getmembers(spec.protocol, inspect.isfunction) if not n.startswith("_")}
    assert set(spec.methods) == methods
    assert all(inspect.getdoc(getattr(spec.protocol, method)) for method in methods)
    assert spec.summary


def test_issues_are_the_trackers_not_the_code_hosts():
    assert CAPABILITIES["code-host"].methods == {"merged_pull_requests": "read"}


def test_only_documents_and_chat_can_write():
    assert {name for name, spec in CAPABILITIES.items() if "write" in spec.modes} == {"chat", "documents"}


def test_chat_meetings_and_calendar_read_in_windows_and_only_chat_posts():
    assert CAPABILITIES["chat"].methods == {"messages": "read", "post": "write"}
    assert CAPABILITIES["meetings"].methods == {"ended": "read", "transcripts": "read", "transcript": "read"}
    assert CAPABILITIES["calendar"].methods == {"events": "read"}
    assert CAPABILITIES["chat"].protocol is Chat
    assert CAPABILITIES["meetings"].protocol is Meetings
    assert CAPABILITIES["calendar"].protocol is Calendar


@pytest.mark.parametrize("name", sorted(set(CAPABILITIES) - {"agent-runner"}))
def test_read_methods_take_only_required_json_shaped_arguments_so_contracts_can_name_them(name):
    """A contract input passes fixed `args` once (ADR 0021): no optional parameter, every one a string or a window."""
    spec = CAPABILITIES[name]
    for method, mode in spec.methods.items():
        if mode != "read":
            continue
        hints = get_type_hints(getattr(spec.protocol, method))
        for parameter in inspect.signature(getattr(spec.protocol, method)).parameters.values():
            if parameter.name == "self":
                continue
            assert parameter.default is inspect.Parameter.empty, f"{name}.{method}: {parameter.name} is optional"
            assert hints[parameter.name] in {str, Window}, f"{name}.{method}: {parameter.name} isn't str or Window"
        windows = [p for p, hint in hints.items() if hint is Window]
        assert len(windows) <= 1, f"{name}.{method} takes {len(windows)} windows; a contract input sets one"


def test_contract_shaped_reads_take_a_window_and_id_reads_serve_operations():
    """Only window reads are contract-shaped; a contract can't know a document or meeting id (ADR 0028)."""
    by_window = {
        (name, method)
        for name, spec in CAPABILITIES.items()
        for method, mode in spec.methods.items()
        if mode == "read" and Window in get_type_hints(getattr(spec.protocol, method)).values()
    }
    assert by_window == {
        ("calendar", "events"),
        ("chat", "messages"),
        ("code-host", "merged_pull_requests"),
        ("mail", "messages"),
        ("meetings", "ended"),
        ("meetings", "transcripts"),
        ("tracker", "issues_updated"),
    }


def test_protocol_results_are_models_with_described_fields():
    for model in (
        Message,
        PostedMessage,
        Meeting,
        Transcript,
        TranscriptSegment,
        Event,
        Participant,
        MailMessage,
        MailContent,
        MailQuery,
    ):
        for field_name, info in model.model_fields.items():
            assert info.description, f"{model.__name__}.{field_name} has no description"


def test_new_datetimes_and_windows_must_be_timezone_aware():
    naive = datetime(2026, 10, 1)  # noqa: DTZ001 - the point of the test
    with pytest.raises(ValidationError):
        Window(since=naive, until=naive)
    with pytest.raises(ValidationError):
        Event(id="e", title="t", start=naive, end=naive)


def test_argument_checks_name_a_str_argument_of_a_read_method():
    for name, spec in CAPABILITIES.items():
        for (method, argument), check in spec.checks.items():
            assert spec.methods.get(method) == "read", f"{name}.{method}"
            assert get_type_hints(getattr(spec.protocol, method))[argument] is str, f"{name}.{method}({argument})"
            assert callable(check)
    assert CAPABILITIES["mail"].checks["messages", "query"] == MailQuery.parse


def test_mail_only_reads():
    assert CAPABILITIES["mail"].methods == {"messages": "read", "read": "read"}
    assert CAPABILITIES["mail"].protocol is Mail
    assert CAPABILITIES["mail"].modes == {"read"}


def test_mail_senders_and_recipients_are_participants_so_packets_drop_their_addresses():
    hints = get_type_hints(MailMessage)
    assert hints["sender"] is Participant
    assert hints["recipients"] == list[Participant]
    with pytest.raises(ValidationError):
        MailMessage(id="m", sender=Participant(), subject="s", received_at=datetime(2026, 10, 1))  # noqa: DTZ001


def test_people_are_names_and_emails_only():
    """ADR 0024: no judgement, count or rank about a person; the email is the directory's join key."""
    assert set(Participant.model_fields) == {"name", "email"}


def test_finds_the_capability_of_a_protocol():
    assert capability_of(Tracker) is CAPABILITIES["tracker"]
    assert capability_of(int) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("tracker:read", Requirement("tracker", frozenset({"read"}))),
        ("documents:read,write", Requirement("documents", frozenset({"read", "write"}))),
        (" documents:write ", Requirement("documents", frozenset({"write"}))),
        ("acme-docs:documents:read", Requirement("documents", frozenset({"read"}), "acme-docs")),
    ],
)
def test_parses_requirements(text, expected):
    assert Requirement.parse(text) == expected


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("tracker", "must read"),
        ("a:b:c:d", "must read"),
        ("spreadsheets:read", "unknown capability 'spreadsheets'"),
        ("tracker:", "modes must be"),
        ("tracker:delete", "modes must be"),
        ("tracker:write", "tracker has no write mode"),
        ("Acme:tracker:read", "adapter 'Acme'"),
    ],
)
def test_refuses_malformed_requirements(text, problem):
    with pytest.raises(ValueError, match=problem):
        Requirement.parse(text)


def test_requirements_print_as_written():
    assert str(Requirement.parse("documents:write,read")) == "documents:read,write"
    assert str(Requirement.parse("acme-docs:documents:read")) == "acme-docs:documents:read"


def test_repeated_capabilities_merge_their_modes():
    assert parse_requirements(["documents:read", "tracker:read", "documents:write"]) == (
        Requirement("documents", frozenset({"read", "write"})),
        Requirement("tracker", frozenset({"read"})),
    )


def test_one_capability_cannot_be_pinned_to_two_adapters():
    with pytest.raises(ValueError, match="one adapter per capability"):
        parse_requirements(["documents:read", "acme-docs:documents:write"])


def test_documents_write_method_is_marked_write():
    assert CAPABILITIES["documents"].methods == {"read": "read", "replace_section": "write"}
    assert CAPABILITIES["documents"].protocol is Documents
