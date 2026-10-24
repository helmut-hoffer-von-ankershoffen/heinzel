"""Measuring drafts (`_measure`), recording what was sent (`WorkflowService.sent`) and the shipped templates."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from heinzel import AgentResult, Config, ContractTemplate, HeinzelError, Module, Settings
from heinzel.modules.workflow import WorkflowService, WorkflowSettings
from heinzel.modules.workflow import _measure as measure
from heinzel.modules.workflow._contracts import read_contract
from heinzel.modules.workflow._starter import from_template, starter

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 30, 17, 0, tzinfo=UTC)
TEMPLATE = Path(__file__).parents[2] / "fixtures" / "playbook" / "templates" / "weekly-report.prose.md"


def test_the_changed_ratio_counts_words_not_whitespace():
    assert measure.changed_ratio("a b c", "a  b\nc") == 0
    assert measure.changed_ratio("", "") == 0
    assert measure.changed_ratio("a b", "") == 1
    assert measure.changed_ratio("a b c d", "x y z w") == 1
    assert 0 < measure.changed_ratio("one two three four", "one two three five") < 0.5
    ratio, before, after, digest = measure.compare("a b", "a b c")
    assert (before, after, digest.startswith("sha256:")) == (2, 3, True)
    assert 0 < ratio < 1


def test_a_draft_without_records_is_measured_as_unknown(tmp_path):
    draft = tmp_path / "20261030T170000Z.md"
    draft.write_text("x")
    run = measure.measure(draft, NOW, None)
    assert (run.drafting_seconds, run.cost_usd, run.inputs, run.changed, run.edit_minutes) == (
        None,
        None,
        {},
        None,
        None,
    )
    broken = tmp_path / "20261030T170000Z.record.json"
    broken.write_text("{not json")
    assert measure.measure(draft, NOW, broken).inputs == {}
    broken.write_text("[1, 2]")
    assert measure.measure(draft, NOW, broken).inputs == {}


def test_drafting_time_falls_back_to_the_agent_session(tmp_path):
    draft = tmp_path / "d.md"
    record = tmp_path / "d.record.json"
    agent = {"duration_seconds": 4, "usage": {"cost_usd": True}}
    record.write_text(json.dumps({"rendered": {"agent": agent, "inputs": {"a": "sha256:1"}}}))
    run = measure.measure(draft, NOW, record)
    assert (run.drafting_seconds, run.cost_usd, run.inputs) == (4.0, None, {"a": "sha256:1"})
    record.write_text(json.dumps({"rendered": {"seconds": 0, "agent": agent}}))
    assert measure.measure(draft, NOW, record).drafting_seconds == 0


def test_the_summary_is_none_until_there_is_something_to_report(tmp_path):
    empty = measure.summary([])
    assert (empty.drafts, empty.median_drafting_seconds, empty.mean_changed, empty.total_edit_minutes) == (
        0,
        None,
        None,
        None,
    )
    runs = [
        measure.RunMeasure(
            draft=tmp_path / f"{i}.md",
            created=NOW,
            drafting_seconds=s,
            cost_usd=None,
            inputs={},
            changed=c,
            edit_minutes=m,
        )
        for i, (s, c, m) in enumerate([(10.0, 0.2, 5.0), (30.0, None, None), (20.0, 0.4, None)])
    ]
    found = measure.summary(runs)
    assert (found.drafts, found.sent, found.median_drafting_seconds, found.total_edit_minutes) == (3, 2, 20.0, 5.0)
    assert found.mean_changed == pytest.approx(0.3)


class Runner:
    def run(self, prompt, *, contract, tools, timeout, budget) -> AgentResult:
        return AgentResult(runner="fake", status="completed", draft="one two three four", duration_seconds=1)


@pytest.fixture
def service(make_kernel, tmp_path) -> WorkflowService:
    me = tmp_path / "me"
    me.mkdir()
    (me / "plain.prose.md").write_text(starter("plain"))
    fakes = Module("fakes", "Test-only runner.")
    fakes.provides("agent-runner", modes={"read"}, name="fake")(Runner)
    config = Config(files=(), data={"capabilities": {"agent_runner": "fake"}, "workflow": {"user_dir": str(me)}})
    kernel = make_kernel(builtins=True, fakes=fakes, config=config)
    settings = Config(files=(), data={}).settings(Settings, "hz")
    return WorkflowService(kernel, kernel.resolve(WorkflowSettings), settings, clock=lambda: NOW)


def test_sent_records_the_change_next_to_the_newest_draft(service, tmp_path):
    assert service.find("plain").valid, service.find("plain").diagnostics
    with pytest.raises(HeinzelError, match="'plain' has no drafts yet"):
        service.sent("plain", tmp_path / "x.md")
    with pytest.raises(HeinzelError, match="a file you saved"):
        service.sent("plain")
    with pytest.raises(HeinzelError, match="a file you saved"):
        service.sent("plain", tmp_path / "x.md", ref="doc")
    rendered = service.run("plain").rendered
    assert rendered is not None
    assert rendered.seconds is not None
    sent = tmp_path / "sent.md"
    sent.write_text("one two three five")
    result = service.sent("plain", sent, edit_minutes=3)
    assert (result.draft, result.draft_words, result.sent_words, result.edit_minutes) == (rendered.draft, 4, 4, 3)
    assert result.record == rendered.draft.with_name(f"{rendered.draft.stem}.sent.json")
    assert [d.draft for d in service.drafts("plain").drafts] == [rendered.draft]  # the record isn't a draft
    shown = service.show("plain").measurements
    assert shown is not None
    assert (shown.sent, shown.mean_changed) == (1, result.changed)
    assert service.sent("plain", sent, draft=rendered.draft).changed == result.changed
    with pytest.raises(HeinzelError, match="isn't a draft of 'plain'"):
        service.sent("plain", sent, draft=tmp_path / "other.md")


def test_sent_refuses_what_isnt_a_small_regular_file(service, tmp_path):
    service.run("plain")
    with pytest.raises(HeinzelError, match="isn't a regular file"):
        service.sent("plain", tmp_path)
    link = tmp_path / "link.md"
    link.symlink_to(tmp_path / "missing.md")
    with pytest.raises(HeinzelError, match="isn't a regular file"):
        service.sent("plain", link)
    large = tmp_path / "large.md"
    large.write_bytes(b"x" * (measure.MAX_SENT_BYTES + 1))
    with pytest.raises(HeinzelError, match="over 1024 KiB"):
        service.sent("plain", large)


def test_sent_refuses_a_linked_drafts_directory(service, tmp_path):
    draft = service.run("plain").rendered
    assert draft is not None
    directory = draft.draft.parent
    moved = tmp_path / "moved"
    directory.rename(moved)
    directory.symlink_to(moved)
    sent = tmp_path / "sent.md"
    sent.write_text("x")
    with pytest.raises(HeinzelError, match="symbolic link"):
        service.sent("plain", sent, draft=directory / draft.draft.name)


def test_an_unreadable_sent_file_is_an_error(service, tmp_path, monkeypatch):
    service.run("plain")
    sent = tmp_path / "sent.md"
    sent.write_text("x")

    def refuse(*_args: object, **_kwargs: object) -> str:
        raise PermissionError(13, "Permission denied", str(sent))

    monkeypatch.setattr(Path, "read_text", refuse)
    with pytest.raises(HeinzelError, match=r"Can't read .*sent\.md: Permission denied"):
        service.sent("plain", sent)


def test_a_template_becomes_a_contract_of_another_name_with_its_assets_filled():
    template = ContractTemplate.from_file(TEMPLATE)
    text = from_template(template, "team-update", {"weekly-report": 'doc "1"'})
    contract = read_contract(Path("team-update.prose.md"), "me", text=text)
    assert contract.valid, contract.diagnostics
    assert contract.frontmatter is not None
    assert contract.frontmatter.hz.inputs["previous_report"].args == {"ref": 'doc "1"'}
    assert contract.frontmatter.hz.schedule is None  # nothing runs on the daemon until the person says so
