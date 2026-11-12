"""Switching found MCP servers off and on: default-on, tombstones that survive a rescan, the first-run notice, and
that the state file holds nothing that connects to a server (ADR 0146). Fake home directories; nothing is run."""

import json
import logging
from collections.abc import Mapping
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.modules.system.claude import ClaudeService, _switch
from heinzel.modules.system.claude._switch import SwitchStore, switch_file

pytestmark = pytest.mark.unit

SECRET = "sk-live-NEVER-STORED"


def configure(
    home: Path, user: Mapping[str, object], project: Mapping[str, object] | None = None, folder: str = "demo"
) -> None:
    """Claude Code's user entries and one project `folder` with its `.mcp.json`; other project folders are removed."""
    for stale in (home / "Code").glob("*/.mcp.json"):
        stale.unlink()
    directory = home / "Code" / folder
    directory.mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text(json.dumps({"mcpServers": user, "projects": {str(directory): {}}}))
    (directory / ".mcp.json").write_text(json.dumps({"mcpServers": project or {}}))


SHARED = {"shared": {"command": "bun", "args": ["run", "server"]}}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    configure(
        root,
        {
            "tickets": {"command": "npx", "args": ["tickets-mcp", SECRET], "env": {"API_KEY": SECRET}},
            "mine": {"command": "hz", "args": ["system", "serve", "mcp"]},
        },
        SHARED,
    )
    return root


@pytest.fixture
def store(tmp_path: Path) -> SwitchStore:
    return SwitchStore(tmp_path / "state" / "claude" / "servers.json")


@pytest.fixture
def service(home: Path, store: SwitchStore) -> ClaudeService:
    return ClaudeService(home, store)


def enabled(service: ClaudeService) -> dict[str, bool]:
    return {s.name: s.enabled for s in service.scan().servers}


def project_id(service: ClaudeService) -> str:
    return next(s.id for s in service.scan().servers if s.name == "shared" and s.scope == "project")


def state(store: SwitchStore) -> dict[str, list[object]]:
    return json.loads(store.path.read_text())


def test_found_servers_are_on_by_default_with_their_scope_and_hz_itself_is_not(service: ClaudeService):
    servers = {s.name: s for s in service.scan().servers}
    assert {name: (s.enabled, s.scope) for name, s in servers.items()} == {
        "tickets": (True, "user"),
        "shared": (True, "project"),
        "mine": (False, "user"),
    }
    assert service.summary().enabled == 2


def test_a_disabled_server_stays_off_after_a_rescan_and_a_new_service(
    service: ClaudeService, home: Path, store: SwitchStore
):
    result = service.switch("code-user:tickets", on=False)
    assert (result.enabled, result.changed, result.scope) == (False, True, "user")
    assert enabled(ClaudeService(home, store)) == {"tickets": False, "shared": True, "mine": False}


def test_a_changed_entry_with_the_same_id_stays_off(service: ClaudeService, home: Path):
    service.switch("code-user:tickets", on=False)
    before = next(s.fingerprint for s in service.scan().servers if s.name == "tickets")
    configure(home, {"tickets": {"type": "http", "url": "https://tickets.example.com/mcp"}})
    after = next(s for s in service.scan().servers if s.name == "tickets")
    assert after.fingerprint != before
    assert after.enabled is False


def test_a_removed_and_re_added_entry_stays_off(service: ClaudeService, home: Path):
    service.switch("code-user:tickets", on=False)
    configure(home, {})
    assert "tickets" not in enabled(service)
    configure(home, {"tickets": {"command": "npx", "args": ["other"]}})
    assert enabled(service)["tickets"] is False


def test_the_same_server_found_in_another_place_stays_off_but_another_shape_does_not(
    service: ClaudeService, home: Path
):
    service.switch(project_id(service), on=False)
    moved = {"shared": {"command": "bun", "args": ["run", "server"]}, "other": {"command": "bun", "args": ["a", "b"]}}
    configure(home, moved, {})
    assert enabled(service) == {"shared": False, "other": True}  # same name and shape: off; same shape only: on


def test_enable_switches_it_back_on_and_removes_every_matching_tombstone(
    service: ClaudeService, home: Path, store: SwitchStore
):
    service.switch(project_id(service), on=False)
    configure(home, {"shared": {"command": "bun", "args": ["run", "server"]}}, {})
    result = service.switch("code-user:shared", on=True)
    assert (result.enabled, result.changed) == (True, True)
    assert enabled(service) == {"shared": True}
    assert json.loads(store.path.read_text())["disabled"] == []


def test_a_remembered_server_that_is_gone_can_still_be_switched_on(service: ClaudeService, home: Path):
    service.switch("code-user:tickets", on=False)
    configure(home, {})
    assert service.switch("code-user:tickets", on=True).changed is True
    configure(home, {"tickets": {"command": "npx"}})
    assert enabled(service)["tickets"] is True


def test_switching_twice_changes_nothing_the_second_time(service: ClaudeService, store: SwitchStore):
    service.switch("code-user:tickets", on=False)
    assert service.switch("code-user:tickets", on=False).changed is False
    assert len(json.loads(store.path.read_text())["disabled"]) == 1
    service.switch("code-user:tickets", on=True)
    assert service.switch("code-user:tickets", on=True).changed is False


def test_an_unknown_id_and_hz_itself_are_refused(service: ClaudeService, store: SwitchStore):
    with pytest.raises(HeinzelError, match="No MCP server has that id"):
        service.switch("code-user:nope", on=False)
    with pytest.raises(HeinzelError, match="No MCP server has that id"):
        service.switch("code-user:nope", on=True)
    with pytest.raises(HeinzelError, match="own MCP server"):
        service.switch("code-user:mine", on=True)
    assert not store.path.exists()


def test_the_state_file_is_private_and_holds_nothing_that_connects(service: ClaudeService, store: SwitchStore):
    store.path.parent.mkdir(parents=True, mode=0o755)
    service.switch("code-user:tickets", on=False)
    service.acknowledge()
    text = store.path.read_text()
    assert SECRET not in text
    assert "npx" not in text
    assert "API_KEY" not in text
    assert set(json.loads(text)) == {"version", "disabled", "allowed", "acknowledged"}
    assert set(json.loads(text)["disabled"][0]) == {"id", "name", "fingerprint"}
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("content", ["{broken", '{"version": 99}', '{"disabled": "all"}', "[]"])
def test_a_state_file_that_cannot_be_trusted_switches_everything_off(
    service: ClaudeService, store: SwitchStore, content: str
):
    store.path.parent.mkdir(parents=True)
    store.path.write_text(content)
    scan = service.scan()
    assert not any(s.enabled for s in scan.servers)
    assert scan.state_problem is not None
    assert scan.notice is None
    assert [c.status.value for c in service.checks()] == ["degraded", "up"]
    with pytest.raises(HeinzelError, match="every server stays switched off"):
        service.switch("code-user:tickets", on=True)
    assert store.path.read_text() == content


def test_a_linked_state_file_is_not_followed(service: ClaudeService, store: SwitchStore, tmp_path: Path):
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text('{"version": 1}')
    store.path.parent.mkdir(parents=True)
    store.path.symlink_to(elsewhere)
    assert not any(s.enabled for s in service.scan().servers)


def test_the_notice_lists_new_servers_until_acknowledged_and_returns_for_a_later_one(
    service: ClaudeService, home: Path
):
    notice = service.scan().notice
    assert notice is not None
    assert (len(notice.servers), notice.project, notice.plugin) == (2, 1, 0)
    assert service.acknowledge().servers == 2
    assert service.scan().notice is None
    configure(home, {"tickets": {"command": "npx"}}, {"shared": {"command": "bun"}, "cloned": {"command": "sh"}})
    again = service.scan().notice
    assert again is not None
    assert [i.rsplit(":", 1)[-1] for i in again.servers] == ["cloned"]
    assert again.project == 1


def test_a_switched_off_server_is_not_in_the_notice(service: ClaudeService):
    service.switch(project_id(service), on=False)
    notice = service.scan().notice
    assert notice is not None
    assert (len(notice.servers), notice.project) == (1, 0)


def test_switching_logs_a_start_and_an_outcome_without_the_id_or_name(
    service: ClaudeService, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger="heinzel.claude")
    service.switch(project_id(service), on=False)
    with pytest.raises(HeinzelError):
        service.switch("code-user:nope", on=False)
    events = [(r.getMessage(), r.__dict__.get("phase"), r.__dict__.get("outcome")) for r in caplog.records]
    assert events == [
        ("claude.server.switch", "start", None),
        ("claude.server.switch", "end", "ok"),
        ("claude.server.switch", "start", None),
        ("claude.server.switch", "end", "error"),
    ]
    done = caplog.records[1].__dict__
    assert (done["on"], done["scope"], done["changed"]) == (False, "project", True)
    assert (
        caplog.records[3].__dict__["error"]
        == "No MCP server has that id. `hz system claude list --json` shows every id."
    )
    logged = " ".join(str(r.__dict__) for r in caplog.records)
    for private in ("shared", "demo", "nope", "code-user", "code-project"):
        assert private not in logged


def test_a_service_without_a_store_lists_default_on_and_cannot_switch(home: Path):
    service = ClaudeService(home)
    assert enabled(service)["tickets"] is True
    with pytest.raises(HeinzelError, match="no state file"):
        service.switch("code-user:tickets", on=False)


def test_switching_one_of_two_alike_entries_never_flips_the_other(
    service: ClaudeService, home: Path, store: SwitchStore
):
    configure(home, SHARED, SHARED)  # the same name and shape twice: yours and the project's
    project = project_id(service)
    service.switch(project, on=False)
    assert {s.id: s.enabled for s in service.scan().servers} == {project: False, "code-user:shared": False}
    again = service.switch("code-user:shared", on=False)  # already off by the other's tombstone: it gets its own
    assert again.changed is False
    assert [t["id"] for t in state(store)["disabled"]] == [project, "code-user:shared"]  # type: ignore[index]
    assert service.switch(project, on=True).changed is True
    assert {s.id: s.enabled for s in service.scan().servers} == {project: True, "code-user:shared": False}
    assert state(store)["allowed"] == [project]
    service.switch(project, on=False)
    assert state(store)["allowed"] == []
    assert not any(s.enabled for s in service.scan().servers)


def test_a_project_folder_that_moved_keeps_its_server_off_until_switched_on(
    service: ClaudeService, home: Path, store: SwitchStore
):
    service.switch(project_id(service), on=False)
    configure(home, {}, SHARED, folder="moved")
    moved = project_id(service)
    assert "moved" in moved
    assert enabled(service) == {"shared": False}  # only the old folder's tombstone, matched by name and shape
    assert service.switch(moved, on=False).changed is False
    assert [t["id"] for t in state(store)["disabled"]][-1] == moved  # type: ignore[index]
    assert service.switch(moved, on=True).changed is True
    assert enabled(service) == {"shared": True}
    assert state(store)["disabled"] == []


def test_a_renamed_entry_is_on_again_and_new_to_the_notice(service: ClaudeService, home: Path):
    service.switch("code-user:tickets", on=False)
    service.acknowledge()
    configure(home, {"tix": {"command": "npx", "args": ["tickets-mcp", SECRET], "env": {"API_KEY": SECRET}}}, SHARED)
    assert enabled(service)["tix"] is True  # documented in ADR 0146: neither the id nor the name is the same
    notice = service.scan().notice
    assert notice is not None
    assert notice.servers == ["code-user:tix"]


def test_a_write_that_would_outgrow_the_limit_is_refused_and_switches_nothing_off(
    service: ClaudeService, store: SwitchStore, monkeypatch: pytest.MonkeyPatch
):
    service.switch("code-user:tickets", on=False)
    before = store.path.read_text()
    monkeypatch.setattr(_switch, "LIMIT", len(before.encode()) + 10)
    with pytest.raises(HeinzelError, match="would grow beyond"):
        service.switch(project_id(service), on=False)
    assert store.path.read_text() == before
    assert enabled(service) == {"tickets": False, "shared": True, "mine": False}
    assert service.scan().state_problem is None


def test_acknowledging_closes_only_what_was_shown_and_forgets_servers_that_are_gone(
    service: ClaudeService, home: Path, store: SwitchStore
):
    assert service.acknowledge(["code-user:tickets", "code-user:never-found"]).servers == 1
    notice = service.scan().notice
    assert notice is not None
    assert notice.servers == [project_id(service)]
    configure(home, {}, SHARED)
    assert service.acknowledge().servers == 1
    assert state(store)["acknowledged"] == [project_id(service)]


def test_no_setting_decides_where_the_switches_are_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import platformdirs

    monkeypatch.setenv("HZ_STATE_DIR", str(tmp_path / "from-env"))
    assert switch_file() == tmp_path / "from-env" / "claude" / "servers.json"
    assert ClaudeService.for_user()._store.path == switch_file()  # type: ignore[union-attr]
    monkeypatch.delenv("HZ_STATE_DIR")
    assert switch_file() == platformdirs.user_state_path("hz") / "claude" / "servers.json"
