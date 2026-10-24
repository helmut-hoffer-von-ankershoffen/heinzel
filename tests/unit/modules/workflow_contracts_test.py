"""Reading contract files: strict YAML, the frontmatter schema, the body and diagnostics with lines (ADR 0021)."""

import re
import textwrap
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from heinzel import JobLet, Window
from heinzel.kernel import schedule_problem
from heinzel.modules.workflow import _contracts as contracts
from heinzel.modules.workflow._contracts import _load, collect, contract_name, read_contract, sections
from heinzel.modules.workflow._models import NAME, Frontmatter, _format_duration, cron_problem

pytestmark = pytest.mark.unit

EXAMPLE = Path(__file__).parents[2] / "fixtures" / "workflows" / "weekly-digest.prose.md"
PATH = Path("digest.prose.md")

BODY = """
### Goal

The digest is current.

### Requires

- `issues`: issues that changed this week.

### Maintains

- `digest`: a short digest.

### Done when

- [ ] Every item links to an issue.
"""


def contract(hz: str = "", top: str = "", body: str = BODY, name: str = "digest") -> str:
    """A contract with a valid default frontmatter; `hz` lines replace or extend the `hz:` block."""
    default_hz = textwrap.dedent(
        """\
        schema: 1
        title: Digest
        requires: [tracker:read]
        inputs:
          issues: {read: tracker.issues_updated, args: {project: APP}, window: 7d}
        outputs:
          digest: {format: markdown}
        """
    )
    hz_block = textwrap.indent(textwrap.dedent(hz) or default_hz, "  ")
    return f"---\nname: {name}\nkind: responsibility\ndescription: A digest.\n{top}hz:\n{hz_block}---\n{body}"


def problems(text: str, path: Path = PATH) -> list[tuple[int, str, str]]:
    return [(d.line, d.code, d.field) for d in read_contract(path, "me", text).diagnostics]


def messages(text: str) -> str:
    return "\n".join(str(d) for d in read_contract(PATH, "me", text).diagnostics)


def test_the_example_contract_is_valid_with_every_key_of_the_format():
    read = read_contract(EXAMPLE, "me")
    assert read.diagnostics == ()
    assert read.valid
    assert read.qualified == "me:weekly-digest"
    assert read.frontmatter is not None
    hz = read.frontmatter.hz
    assert hz.schedule == "0 7 * * MON"
    assert hz.timeout == timedelta(minutes=10)
    assert sorted(hz.requirements()) == ["agent-runner", "code-host", "tracker"]
    assert hz.agent.tools == ["Read", "Grep"]
    assert "Author's note" not in read.body  # comments are for authors, never for the agent
    assert read.body.startswith("# Weekly app digest")


def test_a_minimal_contract_is_valid():
    read = read_contract(PATH, "me", contract())
    assert read.valid, messages(contract())
    assert read.frontmatter is not None
    assert read.frontmatter.hz.schedule is None


def test_yaml_1_1_booleans_dates_and_octals_stay_strings():
    data, _ = _load("a: [on, yes, No, 2026-10-24, 010, '1:20', 1:20, true, False, 0x1f, 7, -1.5e3, .inf, ~, null]")
    assert data["a"][:7] == ["on", "yes", "No", "2026-10-24", "010", "1:20", "1:20"]
    assert data["a"][7:12] == [True, False, 31, 7, -1500.0]
    assert data["a"][12] == float("inf")
    assert data["a"][13:] == [None, None]
    # `on:` is a key under the core schema, so it is reported as the deferred key it is, on its own line
    found = problems(contract(hz="schema: 1\ntitle: T\non: [issues]\noutputs: {d: {}}\n"))
    assert (8, "HZC023", "hz.on") in found


@pytest.mark.parametrize(
    ("frontmatter", "code", "line"),
    [
        ("name: a\nname: b\n", "HZC004", 3),
        ("1: one\n", "HZC004", 2),
        ("name: [unclosed\n", "HZC001", 2),
        ("- a list\n", "HZC001", 2),
        ("", "HZC001", 2),
        ("bad: !!python/name:os.system\n", "HZC004", 2),
    ],
)
def test_broken_yaml_is_reported_on_its_line(frontmatter, code, line):
    assert problems(f"---\n{frontmatter}---\n{BODY}")[0] == (line, code, "frontmatter")


def test_characters_yaml_refuses_are_reported():
    assert problems("---\nname: ￾\n---\n")[0] == (2, "HZC001", "frontmatter")


ESC = "\x1b"


@pytest.mark.parametrize(
    ("where", "text"),
    [
        ("title", f"title: {ESC}[31mred{ESC}[0m"),
        ("osc 8 link", f"title: {ESC}]8;;https://evil.invalid{ESC}\\click{ESC}]8;;{ESC}\\"),
        ("osc 52 clipboard", f"title: {ESC}]52;c;cm0gLXJmIH4={chr(7)}"),
        ("c1 csi", "title: \x9b31mred"),
        ("bell", "title: ding\x07"),
        ("delete", "title: oops\x7f"),
    ],
)
def test_control_characters_are_refused_with_their_line(where, text):
    text = contract(hz=f"schema: 1\n{text}\noutputs: {{digest: {{}}}}\n")
    found = read_contract(PATH, "me", text)
    assert not found.valid, where
    assert [(d.line, d.code, d.message) for d in found.diagnostics] == [
        (7, "HZC005", "control characters aren't allowed")
    ]
    assert found.body == ""
    assert all(ESC not in str(d) and "\x9b" not in str(d) for d in found.diagnostics)


@pytest.mark.parametrize(
    "text",
    ['title: "\\e[31mred"', 'title: "\\x9b31m"', 'title: "a\\u001b]52;c;aGk=\\a"', '"\\e[2J": x'],
)
def test_yaml_escapes_that_decode_to_control_characters_are_refused(text):
    found = read_contract(PATH, "me", contract(hz=f"schema: 1\n{text}\noutputs: {{digest: {{}}}}\n"))
    assert [d.code for d in found.diagnostics] == ["HZC005"]
    assert all(ESC not in str(d) and "\x9b" not in str(d) for d in found.diagnostics)


def test_a_schedule_that_never_fires_is_refused():
    found = read_contract(PATH, "me", contract(hz='schema: 1\ntitle: T\nschedule: "0 0 30 2 *"\noutputs: {d: {}}\n'))
    assert [(d.code, d.message) for d in found.diagnostics if d.field == "hz.schedule"] == [
        ("HZC011", "invalid schedule '0 0 30 2 *': it never fires, e.g. 30 February")
    ]


def test_control_characters_in_the_body_are_refused_too():
    found = read_contract(PATH, "me", contract(body=BODY.replace("### Goal", f"### Goal {ESC}[2J")))
    assert [d.code for d in found.diagnostics] == ["HZC005"]
    assert read_contract(PATH, "me", contract(body=BODY + "\n\tindented with a tab\n")).valid


def test_a_file_without_frontmatter_or_with_an_unclosed_one_is_reported():
    assert problems("# Just Markdown\n")[0][1] == "HZC001"
    assert problems("---\nname: digest\n")[0][1] == "HZC001"


def test_an_unreadable_file_is_reported(tmp_path):
    assert [d.code for d in read_contract(tmp_path / "missing.prose.md", "me").diagnostics] == ["HZC001"]


def test_a_newer_schema_asks_for_an_update():
    found = read_contract(PATH, "me", contract(hz="schema: 2\n"))
    assert next((d.code, d.hint) for d in found.diagnostics) == ("HZC003", "run `hz system update`")
    assert not found.valid


def test_the_name_must_equal_the_file_stem_and_the_suffix_is_required():
    assert (2, "HZC010", "name") in problems(contract(name="other"))
    assert (1, "HZC010", "file") in problems(contract(), Path("digest.md"))
    assert contract_name(Path("x/digest.prose.md")) == "digest"
    assert contract_name(Path("digest.md")) == "digest"


@pytest.mark.parametrize(
    ("hz", "field", "text"),
    [
        ("schema: 1\noutputs: {d: {}}\n", "hz.title", "`title` is required"),
        ("schema: 1\ntitle: T\noutputs: {d: {}}\ncolour: blue\n", "hz.colour", "unknown key `colour`"),
        ("schema: 1\ntitle: T\nowner: '@mira'\noutputs: {d: {}}\n", "hz.owner", "people (ADR 0024)"),
        ("schema: 1\ntitle: T\nscope: team\noutputs: {d: {}}\n", "hz.scope", "leases in the state repository"),
        ("schema: 1\ntitle: T\noutputs: {d: {target: 'state:x'}}\n", "hz.outputs.d.target", "hz inbox"),
        ("schema: 1\ntitle: T\noutputs: {d: {}}\nagent: {model: deep}\n", "hz.agent.model", "configuration"),
        ("schema: 1\ntitle: T\noutputs: {d: {}, e: {}}\n", "hz.outputs", "exactly one output"),
        ("schema: 1\ntitle: T\noutputs: {}\n", "hz.outputs", "exactly one output"),
        ("schema: 1\ntitle: T\ntimeout: 3h\noutputs: {d: {}}\n", "hz.timeout", "at most 2h"),
        ("schema: 1\ntitle: T\ntimeout: soon\noutputs: {d: {}}\n", "hz.timeout", "positive whole number"),
        (
            "schema: 1\ntitle: T\noutputs: {d: {}}\npublishes: [{action: notes.publish, output: x}]\n",
            "hz.publishes.0.output",
            "no output named 'x'",
        ),
        (
            "schema: 1\ntitle: T\noutputs: {d: {}}\npublishes: [{action: n.p, output: d, auto_approve: p}]\n",
            "hz.publishes.0.auto_approve",
            "approval policies",
        ),
    ],
)
def test_schema_violations_name_the_key_and_say_why(hz, field, text):
    found = read_contract(PATH, "me", contract(hz=hz)).diagnostics
    assert any(d.field == field and text in d.message for d in found), messages(contract(hz=hz))


@pytest.mark.parametrize(
    ("requires", "text"),
    [
        ("[documents:write]", "a contract only reads"),
        ("[github:tracker:read]", "pins the adapter 'github'"),
        ("[tracker:read, tracker:read]", "required twice"),
        ("[mail:read]", "unknown capability 'mail'"),
    ],
)
def test_requirements_only_read_never_pin_and_never_repeat(requires, text):
    hz = f"schema: 1\ntitle: T\nrequires: {requires}\noutputs: {{d: {{}}}}\n"
    found = read_contract(PATH, "me", contract(hz=hz, body=BODY.replace("### Requires", "### Steps"))).diagnostics
    assert any(d.field.startswith("hz.requires.") and text in d.message for d in found), [str(d) for d in found]


def test_an_explicit_agent_runner_requirement_is_allowed():
    hz = "schema: 1\ntitle: T\nrequires: [agent-runner:read]\noutputs: {digest: {}}\n"
    read = read_contract(PATH, "me", contract(hz=hz, body=BODY.replace("### Requires", "### Steps")))
    assert read.valid, [str(d) for d in read.diagnostics]


INPUT = (
    "schema: 1\ntitle: T\nrequires: [tracker:read, documents:read]\noutputs: {{digest: {{}}}}\ninputs:\n  issues: {}\n"
)


@pytest.mark.parametrize(
    ("spec", "field", "text"),
    [
        ("{read: tracker.issues_updated, args: {project: APP}}", "hz.inputs.issues.window", "set `window`"),
        ("{read: documents.read, args: {ref: x}, window: 7d}", "hz.inputs.issues.window", "takes no time window"),
        ("{read: tracker.issues_updated, window: 7d}", "hz.inputs.issues.args", "needs `args: {project"),
        (
            "{read: tracker.issues_updated, args: {project: APP, label: x}, window: 7d}",
            "hz.inputs.issues.args.label",
            "no argument 'label'",
        ),
        (
            "{read: tracker.issues_updated, args: {project: [1]}, window: 7d}",
            "hz.inputs.issues.args.project",
            "valid string",
        ),
        ("{read: tracker.close, args: {}}", "hz.inputs.issues.read", "has no method 'close'"),
        ("{read: documents.replace_section, args: {}}", "hz.inputs.issues.read", "writes"),
        ("{read: agent-runner.run, args: {}}", "hz.inputs.issues.read", "not a capability an input can read"),
        ("{read: mail.inbox, args: {}}", "hz.inputs.issues.read", "not a capability an input can read"),
        (
            "{read: code-host.merged_pull_requests, args: {repository: a/b}, window: 7d}",
            "hz.inputs.issues.read",
            "needs `code-host:read`",
        ),
        ("{source: 'jira:APP'}", "hz.inputs.issues.source", "never a vendor URI"),
    ],
)
def test_inputs_name_a_read_method_of_a_required_capability_with_its_arguments(spec, field, text):
    found = read_contract(PATH, "me", contract(hz=INPUT.format(spec))).diagnostics
    assert any(d.field == field and text in d.message for d in found), [str(d) for d in found]


MEETINGS_HZ = """\
schema: 1
title: One-on-one prep
requires: [chat:read, meetings:read, calendar:read]
inputs:
  issues: {read: chat.messages, args: {channel: "#platform"}, window: 7d}
  thread: {read: chat.thread, args: {channel: "#platform", message: "1729.0001"}}
  meetings: {read: meetings.ended, window: 7d}
  transcript: {read: meetings.transcript, args: {meeting: m-1}}
  events: {read: calendar.events, window: 7d}
outputs:
  digest: {format: markdown}
"""

MEETINGS_BODY = BODY.replace(
    "- `issues`: issues that changed this week.",
    "- `issues`: what the channel discussed this week.\n- `thread`: the thread to prepare.\n"
    "- `meetings`: meetings that ended this week.\n- `transcript`: what the last one-on-one said.\n"
    "- `events`: this week's calendar.",
)


def test_inputs_can_name_chat_meetings_and_calendar_read_methods():
    read = read_contract(PATH, "me", contract(hz=MEETINGS_HZ, body=MEETINGS_BODY))
    assert read.diagnostics == (), [str(d) for d in read.diagnostics]
    assert read.frontmatter is not None
    assert sorted(read.frontmatter.hz.requirements()) == ["agent-runner", "calendar", "chat", "meetings"]


@pytest.mark.parametrize(
    ("spec", "text"),
    [
        ("{read: chat.post, args: {channel: x, text: y}}", "writes"),
        ("{read: chat.messages, args: {channel: x}}", "set `window`"),
        ("{read: meetings.transcript, args: {meeting: m-1}, window: 7d}", "takes no time window"),
        ("{read: calendar.events, args: {calendar: work}, window: 7d}", "no argument 'calendar'"),
        ("{read: meetings.ended, window: 7d}", "needs `meetings:read`"),
    ],
)
def test_inputs_of_the_new_capabilities_are_checked_like_the_others(spec, text):
    found = read_contract(PATH, "me", contract(hz=INPUT.format(spec))).diagnostics
    assert any(text in d.message for d in found), [str(d) for d in found]


def test_an_invalid_schedule_is_its_own_code():
    found = read_contract(PATH, "me", contract(hz="schema: 1\ntitle: T\nschedule: every monday\noutputs: {d: {}}\n"))
    assert [d.code for d in found.diagnostics if d.field == "hz.schedule"] == ["HZC011"]
    assert cron_problem("@daily") is None
    assert cron_problem("0 7 * * MON") is None
    assert cron_problem("61 * * * *") is not None


def test_diagnostics_point_at_the_line_of_the_key():
    text = contract(hz="schema: 1\ntitle: T\noutputs:\n  digest:\n    format: pdf\n")
    found = read_contract(PATH, "me", text).diagnostics
    assert [(d.line, d.field) for d in found] == [(10, "hz.outputs.digest.format")]
    assert str(found[0]).startswith("digest.prose.md:10  HZC002  hz.outputs.digest.format")
    assert "hint: see docs/schemas/contract-frontmatter-1.json" in str(found[0])


def test_the_body_needs_a_goal_and_postconditions():
    found = problems(contract(body="\n### Requires\n\n- `issues`: x\n\n### Maintains\n\n- `digest`: y\n"))
    assert {code for _, code, _ in found} == {"HZC101", "HZC107"}
    found = problems(contract(body=BODY.replace("- [ ] Every item links to an issue.", "Nothing listed.")))
    assert [code for _, code, _ in found] == ["HZC107"]


def test_retired_and_unknown_sections_are_reported():
    body = BODY + "\n### Criteria\n\nold\n\n### Tools\n\n- cli:gh\n\n### Notes\n\nsomething\n"
    found = read_contract(PATH, "me", contract(body=body)).diagnostics
    assert [(d.code, d.severity, d.field) for d in found] == [
        ("HZC106", "error", "### Criteria"),
        ("HZC023", "error", "### Tools"),
        ("HZC105", "warning", "### Notes"),
    ]


def test_body_items_must_match_inputs_and_outputs():
    found = problems(contract(body=BODY.replace("`issues`", "`tickets`")))
    assert [(code, where) for _, code, where in found] == [("HZC103", "### Requires")]
    found = problems(
        contract(body=BODY.replace("### Requires", "### Steps").replace("### Maintains", "### Continuity"))
    )
    assert sorted(where for _, _, where in found) == ["### Maintains", "### Requires"]


def test_headings_in_fenced_code_and_comments_are_not_sections():
    body = BODY + "\n### Steps\n\n~~~markdown\n```\n### Criteria\n~~~\n\n<!--\n### Execution\n-->\n"
    assert read_contract(PATH, "me", contract(body=body)).valid


def test_sections_end_at_a_higher_level_heading_and_keep_their_line():
    found = sections("intro\n### Goal\ntext\n## Other\nignored\n### Done when\n- [ ] x\n#### facet\nmore\n", 10)
    assert [(s.name, s.line, s.text) for s in found] == [
        ("Goal", 11, "text"),
        ("Done when", 15, "- [ ] x\n#### facet\nmore"),
    ]


def test_extension_keys_are_ignored():
    assert read_contract(PATH, "me", contract(top="x-anything: {nested: true}\n")).valid


def test_input_arguments_add_the_window_ending_at_the_run():
    read = read_contract(PATH, "me", contract())
    assert read.frontmatter is not None
    until = datetime(2026, 10, 24, tzinfo=UTC)
    arguments = read.frontmatter.hz.inputs["issues"].arguments(until)
    assert arguments == {"project": "APP", "window": Window(since=until - timedelta(days=7), until=until)}


def test_durations_serialize_the_way_they_are_written():
    read = read_contract(EXAMPLE, "me")
    assert read.frontmatter is not None
    dumped = read.frontmatter.model_dump(mode="json", by_alias=True)
    assert dumped["hz"]["timeout"] == "10m"
    assert dumped["hz"]["inputs"]["merged"]["window"] == "7d"
    assert dumped["hz"]["schema"] == 1
    assert _format_duration(timedelta(seconds=90)) == "90s"
    assert _format_duration(timedelta(hours=2)) == "2h"
    assert Frontmatter.model_validate(dumped) == read.frontmatter


def test_collect_finds_contract_files_only(tmp_path):
    (tmp_path / "b.prose.md").write_text("")
    (tmp_path / "a.prose.md").write_text("")
    (tmp_path / "notes.md").write_text("")
    (tmp_path / "dir.prose.md").mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside.prose.md"
    outside.write_text("")
    (tmp_path / "linked.prose.md").symlink_to(outside)
    assert [p.name for p in collect(tmp_path)] == ["a.prose.md", "b.prose.md"]
    assert collect(tmp_path / "missing") == []


# Robustness: one hostile or broken file must stay cheap and never stop anything.

ALIAS_BOMB = "a: &a [x, x, x, x, x, x, x, x, x]\n" + "".join(
    f"{chr(98 + i)}: &{chr(98 + i)} [*{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}]\n"
    for i in range(9)
)


def test_an_alias_bomb_is_refused_before_anything_expands():
    assert len(ALIAS_BOMB) < 1024
    started = time.monotonic()
    found = read_contract(PATH, "me", f"---\n{ALIAS_BOMB}---\n{BODY}").diagnostics
    assert time.monotonic() - started < 1
    assert [(d.code, d.message) for d in found][:1] == [("HZC004", "anchors and aliases aren't allowed")]


def test_deep_nesting_is_refused_fast():
    started = time.monotonic()
    found = read_contract(PATH, "me", "---\nx: " + "[" * 5000 + "]" * 5000 + "\n---\n" + BODY).diagnostics
    assert time.monotonic() - started < 1
    assert found[0].code == "HZC004"
    assert "nested deeper than 32 levels" in found[0].message


def test_too_many_values_are_refused():
    found = read_contract(PATH, "me", "---\nx: [" + ", ".join(["1"] * 10_001) + "]\n---\n").diagnostics
    assert "more than 10000 values" in found[0].message


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("!!set {a, b}", "tags aren't allowed on lists and maps"),
        ("!!binary aGVsbG8=", "the tag tag:yaml.org,2002:binary isn't allowed"),
        ("!custom x", "the tag !custom isn't allowed"),
    ],
)
def test_tags_beyond_the_core_scalars_are_refused(value, text):
    found = read_contract(PATH, "me", f"---\nx: {value}\n---\n").diagnostics
    assert (found[0].code, found[0].line) == ("HZC004", 2)
    assert text in found[0].message
    assert read_contract(PATH, "me", contract(top="x-number: !!int 3\n")).valid


def test_a_large_file_is_never_read(tmp_path):
    big = tmp_path / "big.prose.md"
    big.write_bytes(b"---\n" + b"#" * (300 * 1024))
    assert [d.message for d in read_contract(big, "me").diagnostics] == [
        "the file is 300 KiB; contracts are at most 256 KiB"
    ]
    assert "contracts are at most 256 KiB" in read_contract(PATH, "me", "x" * (257 * 1024)).diagnostics[0].message


def test_reading_a_contract_never_raises(monkeypatch):
    def explode(*_: object) -> None:
        raise RecursionError

    monkeypatch.setattr(contracts, "_read", explode)
    found = read_contract(PATH, "me", contract())
    assert (found.valid, [d.code for d in found.diagnostics]) == (False, ["HZC001"])
    assert "RecursionError" in found.diagnostics[0].message


def test_an_unquoted_cron_expression_gets_a_hint():
    found = read_contract(PATH, "me", contract(hz="schema: 1\ntitle: T\nschedule: */5 * * * *\noutputs: {d: {}}\n"))
    assert found.diagnostics[0].hint == 'quote the cron expression: schedule: "0 7 * * MON"'
    assert "@hourly" in str(cron_problem("@reboot"))


def test_every_unsupported_key_is_reported_at_once():
    hz = (
        "schema: 1\ntitle: T\nowner: '@mira'\ntags: [a]\ncatch_up: all\noutputs: {digest: {target: x}}\n"
        "agent: {model: deep}\npublishes: [{action: a.b, output: digest, auto_approve: p}]\n"
    )
    found = read_contract(PATH, "me", contract(hz=hz, body=BODY.replace("### Requires", "### Steps"))).diagnostics
    assert sorted((d.code, d.field) for d in found) == [
        ("HZC023", "hz.agent.model"),
        ("HZC023", "hz.catch_up"),
        ("HZC023", "hz.outputs.digest.target"),
        ("HZC023", "hz.owner"),
        ("HZC023", "hz.publishes.0.auto_approve"),
        ("HZC023", "hz.tags"),
    ]


@pytest.mark.parametrize(
    ("hz", "field", "text"),
    [
        ("retries: true", "hz.retries", "valid integer"),
        ("retries: '1'", "hz.retries", "valid integer"),
        ("timeout: 10", "hz.timeout", "write `10m`"),
        ("agent: {max_usd: .inf}", "hz.agent.max_usd", "finite number"),
    ],
)
def test_numbers_and_durations_are_strict(hz, field, text):
    found = read_contract(PATH, "me", contract(hz=f"schema: 1\ntitle: T\n{hz}\noutputs: {{d: {{}}}}\n")).diagnostics
    assert any(d.field == field and text in d.message for d in found), [str(d) for d in found]


def test_a_heading_may_end_in_a_hash_sign():
    found = sections("### C#\ntext\n### Goal ##\nmore\n", 1)
    assert [s.name for s in found] == ["C#", "Goal"]


def test_a_missing_section_is_reported_on_the_bodys_last_line():
    text = contract(body="\n### Goal\n\nok\n\n### Requires\n\n- `issues`: x\n\n### Maintains\n\n- `digest`: y\n\n\n")
    done = next(d for d in read_contract(PATH, "me", text).diagnostics if d.code == "HZC107")
    assert done.line == text.rstrip().count("\n") + 1


@pytest.mark.parametrize("name", ["a", "digest", "weekly-app-digest", "a1-b2", "x" * 64])
def test_every_contract_name_makes_a_valid_job_name(name):
    assert re.fullmatch(NAME, name)
    assert JobLet(name=f"team-{name}", func=lambda: None, summary="s", schedule="@daily").name.endswith(name)


@pytest.mark.parametrize("name", ["A", "1a", "a-", "a--b", "a_b"])
def test_names_the_job_pattern_allows_but_contracts_dont(name):
    assert not re.fullmatch(NAME, name)


def test_a_schedule_with_non_ascii_digits_is_refused_like_the_kernel_refuses_it():
    fullwidth = chr(0xFF10) + " 7 * * MON"
    found = read_contract(
        PATH, "me", contract(hz=f'schema: 1\ntitle: T\nschedule: "{fullwidth}"\noutputs: {{d: {{}}}}\n')
    )
    assert [d.code for d in found.diagnostics if d.field == "hz.schedule"] == ["HZC011"]
    assert schedule_problem(fullwidth) is not None
    assert cron_problem("0 7 * * MON") is None
