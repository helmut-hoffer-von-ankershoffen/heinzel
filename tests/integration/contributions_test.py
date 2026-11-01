"""Contributions end to end (ADR 0056): the CLI and the web head list them, the REST head never does.

The built-in ones are enough: the daemon's status item and the org agenda's, which degrades without a calendar.
"""

import json
from typing import Annotated

import pytest
from fastapi.testclient import TestClient
from pydantic import Field
from typer.testing import CliRunner

from heinzel import Kernel, Module, RunOperation, StatusItem, WidgetAction
from heinzel.heads import api, web
from heinzel.heads.cli import build
from heinzel.kernel import boot

pytestmark = pytest.mark.integration

TOKEN = "s3ssion-token-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def kernel() -> Kernel:
    return boot()


def hz(kernel: Kernel, *args: str) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def test_the_cli_lists_the_built_in_status_items_with_their_slots(kernel):
    code, entries = hz(kernel, "system", "ui", "status")
    assert code == 0
    by_id = {entry["id"]: entry for entry in entries}  # type: ignore[union-attr]
    assert by_id["system.daemon:daemon"]["item"]["text"] == "Daemon not installed"
    assert by_id["system.daemon:daemon"]["slots"] == {"footer": True}
    agenda = by_id["org:meeting"]
    # Optional: without a usable calendar it degrades, never fails. Which one depends on the installed plugins: none
    # bound, or the only calendar adapter bound by itself (ADR 0055) but not signed in.
    assert agenda["problem"] is None
    assert agenda["item"]["text"] in {"No calendar", "Calendar unavailable"}
    assert agenda["item"]["detail"][0]["tone"] == "warn"


def test_the_listing_runs_nothing_and_hide_writes_the_choice(kernel):
    code, listed = hz(kernel, "system", "ui", "list")
    assert code == 0
    assert {"id": "org:meeting", "module": "org", "kind": "status", "title": "Meeting"}.items() <= next(
        info
        for info in listed
        if info["id"] == "org:meeting"  # type: ignore[union-attr]
    ).items()
    code, after = hz(kernel, "system", "ui", "hide", "org:meeting")
    assert code == 0
    assert next(info for info in after if info["id"] == "org:meeting")["slots"] == {"footer": False}  # type: ignore[union-attr]
    code, refused = hz(kernel, "system", "ui", "hide", "org:meeting", "--slot", "today")
    assert code == 1
    assert "org:meeting shows in footer" in str(refused)


def test_the_web_head_serves_them_behind_its_token(kernel):
    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000")
    assert client.get("/api/system/ui/status").status_code == 401
    answer = client.get("/api/system/ui/status", params={"item": "system.daemon:daemon"}, headers=AUTH)
    assert answer.status_code == 200
    (entry,) = answer.json()
    install = entry["item"]["actions"][0]["effect"]
    assert install["kind"] == "operation"  # the app runs it after a confirmation; it types nothing into a terminal
    assert install["call"]["path"] == "/system/daemon/install"
    assert install["call"]["command"] == "hz system daemon install"
    assert install["call"]["read_only"] is False
    assert client.get("/api/system/ui/actions", headers=AUTH).status_code == 200
    assert client.get("/api/system/ui/widgets", headers=AUTH).status_code == 200
    shown = client.post("/api/system/ui/hide", params={"item": "system.daemon:daemon"}, headers=AUTH)
    assert shown.status_code == 200
    assert client.get("/api/system/ui/status", params={"item": "nope"}, headers=AUTH).status_code == 400


def acme() -> Module:
    """A module whose status item offers a write the app runs only after the person confirms it."""
    module = Module("acme", "Acme reviews.")
    synced: list[bool] = []

    @module.operation
    def report() -> int:
        """Count the open reviews."""
        return len(synced)

    @module.operation(read_only=False)
    def sync(dry_run: Annotated[bool, Field(description="Only show.")] = False) -> int:
        """Sync the reviews."""
        synced.append(dry_run)
        return len(synced)

    @module.status(source="report")
    def reviews(count: int) -> StatusItem:
        return StatusItem(
            text=f"Synced {count} times",
            actions=[WidgetAction(label="Sync", effect=RunOperation(operation="sync", arguments={"dry_run": True}))],
        )

    return module


def test_a_confirmed_call_reaches_the_operation_the_server_named(make_kernel):
    client = TestClient(
        web.build(make_kernel(builtins=True, acme=acme()), port=8000, token=TOKEN, static=None),
        base_url="http://127.0.0.1:8000",
    )
    (entry,) = client.get("/api/system/ui/status", params={"item": "acme:reviews"}, headers=AUTH).json()
    call = entry["item"]["actions"][0]["effect"]["call"]
    assert call == {
        "method": "POST",
        "path": "/acme/sync",
        "query": {"dry_run": True},
        "body": None,
        "command": "hz acme sync --dry-run true",
        "summary": "Sync the reviews.",
        "read_only": False,
    }
    assert client.post(f"/api{call['path']}", params=call["query"], headers=AUTH).json() == 1
    (entry,) = client.get("/api/system/ui/status", params={"item": "acme:reviews"}, headers=AUTH).json()
    assert entry["item"]["text"] == "Synced 1 times"


def test_the_rest_head_has_none_of_them(kernel):
    client = TestClient(api.build(kernel, port=8000), base_url="http://127.0.0.1:8000")
    for path in ("status", "actions", "widgets", "list"):
        assert client.get(f"/system/ui/{path}").status_code == 404
    assert client.post("/system/ui/hide", params={"item": "org:meeting"}).status_code == 404
