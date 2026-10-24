"""The adapter bound through configuration, used by requesters through the CLI, with a fake Slack. Offline."""

import json
import logging
from datetime import datetime

import pytest
from typer.testing import CliRunner

from heinzel import Chat, Config, Kernel, Message, Module, PostedMessage, Window
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

TOKEN = "xoxp-integration-token-never-shown"
CHANNEL = "C0123456789"
BOUND = Config(files=(), data={"capabilities": {"chat": "slack"}})
SINCE, UNTIL = "2026-10-01T00:00:00+00:00", "2026-10-08T00:00:00+00:00"
IN_WINDOW = "1790900000.000100"


def digest() -> Module:
    """A test-only requester, as a workflow plugin would declare it: capabilities, never a vendor."""
    module = Module("digest", "Test-only digest of a channel.", requires=["chat:read"])

    @module.operation
    def week(chat: Chat, channel: str, since: str, until: str) -> list[Message]:
        """List the messages of a window."""
        window = Window(since=datetime.fromisoformat(since), until=datetime.fromisoformat(until))
        return chat.messages(channel, window)

    @module.operation(read_only=False, requires=["chat:write"])
    def announce(chat: Chat, channel: str, text: str) -> PostedMessage:
        """Post a text."""
        return chat.post(channel, text)

    @module.operation
    def sneak(chat: Chat, channel: str) -> PostedMessage:
        """Try to post from a read-only operation."""
        return chat.post(channel, "sneaky")

    return module


def hz(kernel: Kernel, *args: str) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


@pytest.fixture
def slack(fake_slack, monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_SLACK_SLACK_TOKEN", TOKEN)
    fake_slack.history = [fake_slack.message(IN_WINDOW, "<@U0ADA> shipped <https://acme.example|it> \u202e")]
    return fake_slack


def test_a_requester_reads_messages_through_the_bound_adapter(slack_kernel, slack):
    code, found = hz(slack_kernel(BOUND, digest=digest()), "digest", "week", "#widgets", SINCE, UNTIL)
    assert code == 0, found
    assert found == [
        {
            "channel": CHANNEL,
            "id": IN_WINDOW,
            "author": {"name": "Ada", "email": "ada@acme.example"},
            "posted_at": "2026-10-02T00:13:20.000100Z",
            "text": "@Ada shipped [it](https://acme.example) ",
            "thread": None,
            "reply_count": 0,
            "url": f"https://acme.slack.com/archives/{CHANNEL}/p1790900000000100",
        }
    ]


def test_a_publish_operation_posts_without_paging_anyone(slack_kernel, slack):
    code, posted = hz(slack_kernel(BOUND, digest=digest()), "digest", "announce", CHANNEL, "<!channel> @here done")
    assert code == 0, posted
    assert posted["channel"] == CHANNEL
    assert slack.posted[0]["text"] == "&lt;!channel&gt; @\u2060here done"


def test_a_read_only_operation_cannot_post(slack_kernel, slack):
    code, output = hz(slack_kernel(BOUND, digest=digest()), "digest", "sneak", CHANNEL)
    assert code != 0
    assert "chat" in str(output)
    assert slack.posted == []


def test_the_token_is_sent_to_slack_and_appears_in_no_output(slack_kernel, slack, caplog):
    caplog.set_level(logging.DEBUG)
    kernel = slack_kernel(BOUND, digest=digest())
    assert hz(kernel, "digest", "week", CHANNEL, SINCE, UNTIL)[0] == 0
    assert {r.headers["Authorization"] for r in slack.requests} == {f"Bearer {TOKEN}"}
    for command in (("system", "doctor"), ("system", "info"), ("system", "capability", "list"), ("slack", "info")):
        code, output = hz(kernel, *command)
        assert code == 0, command
        assert TOKEN not in json.dumps(output), command
    assert TOKEN not in caplog.text


def test_a_rejected_token_fails_the_command_with_the_fix_and_without_the_token(slack_kernel, slack, caplog):
    caplog.set_level(logging.DEBUG)
    slack.queue("auth.test", slack.error("invalid_auth", detail=TOKEN))
    code, output = hz(slack_kernel(BOUND, digest=digest()), "digest", "week", CHANNEL, SINCE, UNTIL)
    assert code != 0
    assert "hz system credential set slack-token" in str(output)
    assert TOKEN not in str(output)
    assert TOKEN not in caplog.text


def test_a_token_with_a_line_break_inside_fails_with_the_fix_and_never_shows_it(
    slack_kernel, fake_slack, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HZ_CREDENTIAL_SLACK_SLACK_TOKEN", "xoxp-first\r\nX-Injected: second")
    code, output = hz(slack_kernel(BOUND, digest=digest()), "digest", "week", CHANNEL, SINCE, UNTIL)
    assert code != 0
    assert "hz system credential set slack-token" in str(output)
    for part in ("xoxp-first", "X-Injected", "second"):
        assert part not in str(output)
        assert part not in caplog.text
    assert fake_slack.requests == []


def test_a_rate_limit_is_honoured_and_the_read_completes(slack_kernel, slack):
    slack.queue("conversations.history", slack.error("ratelimited", 429, {"retry-after": "2"}))
    code, found = hz(slack_kernel(BOUND, digest=digest()), "digest", "week", CHANNEL, SINCE, UNTIL)
    assert code == 0, found
    assert slack.slept == [2]
    assert len(found) == 1


def test_unbound_the_requester_fails_and_doctor_names_the_binding(slack_kernel, fake_slack):
    kernel = slack_kernel(digest=digest())
    code, output = hz(kernel, "digest", "week", CHANNEL, SINCE, UNTIL)
    assert code != 0
    assert 'chat = "slack" (installed: slack)' in str(output)
    assert fake_slack.requests == []
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    capability = {c["name"]: c for c in report["checks"]["system.capability"]}
    assert capability["chat:read for digest"]["status"] == "degraded"
    assert {c["name"]: c for c in report["checks"]["slack"]}["bindings"]["status"] == "degraded"


def test_doctor_reports_the_binding_and_a_missing_token_without_contacting_slack(slack_kernel, fake_slack):
    code, report = hz(slack_kernel(BOUND), "system", "doctor")
    assert code == 0
    checks = {c["name"]: c for c in report["checks"]["slack"]}
    assert checks["chat"]["detail"] == "served by slack"
    assert checks["api"]["status"] == "degraded"
    assert checks["api"]["fix"] == "hz system credential set slack-token"
    credential = {c["name"]: c for c in report["checks"]["system.credential"]}
    assert credential["credential slack/slack-token"]["status"] == "degraded"
    assert fake_slack.requests == []


def test_capability_list_shows_the_adapter_reading_and_writing_on_slack_hosts(slack_kernel):
    code, report = hz(slack_kernel(BOUND), "system", "capability", "list")
    assert code == 0
    assert {c["name"]: c["bound"] for c in report["capabilities"]}["chat"] == "slack"
    ours = [(a["capability"], a["modes"], a["hosts"]) for a in report["adapters"] if a["adapter"] == "slack"]
    assert ours == [("chat", ["read", "write"], ["slack.com", "*.slack.com"])]


def test_a_project_file_can_neither_bind_nor_redirect_the_token_nor_change_its_kind(
    slack_kernel, slack, tmp_path, monkeypatch
):
    user = tmp_path / "user"
    user.mkdir()
    (user / "config.toml").write_text('[capabilities]\nchat = "slack"\n', encoding="utf-8")
    monkeypatch.setenv("HZ_CONFIG_HOME", str(user))
    project = tmp_path / "project"
    project.mkdir()
    (project / "hz.toml").write_text(
        '[capabilities]\nchat = "evil"\n[slack]\napi_url = "https://evil.slack.com/api"\ntoken_kind = "bot"\n',
        encoding="utf-8",
    )
    kernel = slack_kernel(Config.load(project), digest=digest())
    code, found = hz(kernel, "digest", "week", CHANNEL, SINCE, UNTIL)
    assert code == 0, found
    assert all(r.url.startswith("https://slack.com/api/") for r in slack.requests)
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    names = [c["name"] for c in report["checks"]["system.doctor"]]
    assert "project setting slack.api_url" in names
    assert "project setting slack.token_kind" in names
    assert "evil" not in json.dumps(report["checks"]["system.doctor"])
