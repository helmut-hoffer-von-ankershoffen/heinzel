"""Only a fresh receipt claim permits installation; uncertain acknowledgments never permit replay."""

import errno
import json
import os

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation
from heinzel.modules.system.plugin import _receipts
from heinzel.modules.system.plugin._models import PluginCandidate, PluginChange, PluginSettings
from heinzel.modules.system.plugin._receipts import PluginReceipts
from heinzel.modules.system.plugin._service import PluginService

pytestmark = pytest.mark.unit


@pytest.fixture
def candidate():
    return PluginCandidate(
        spec="heinzel-harbor",
        requirement="heinzel-harbor==1.0",
        distribution="heinzel-harbor",
        version="1.0",
        summary="A fictional plugin.",
        source="pypi",
        url="https://example.com/harbor",
        publisher=None,
        first_party=False,
        private=None,
        revision=None,
        installed_version=None,
        warnings=[],
    )


@pytest.mark.parametrize("other_owner", [False, True])
def test_a_live_running_receipt_cannot_be_claimed_again(tmp_path, candidate, other_owner):
    receipts = PluginReceipts(tmp_path)
    preview = receipts.create(candidate)
    assert receipts.claim(preview.operation_id).status == "running"
    if other_owner:
        path = receipts._path(preview.operation_id)
        record = json.loads(path.read_text())
        record["owner_pid"] = os.getppid()
        path.write_text(json.dumps(record))

    with pytest.raises(HeinzelError, match="already running"):
        receipts.claim(preview.operation_id)
    assert receipts.get(preview.operation_id).status == "running"


def test_an_interleaved_retry_between_installer_unlock_and_unknown_runs_uv_only_once(
    tmp_path, candidate, make_kernel, monkeypatch
):
    installation = Installation(Progress(), prefix=tmp_path / "sdk", uv="uv")
    service = PluginService(make_kernel(), installation, PluginSettings(github_token=None, catalog_sources=[]))
    preview = service._receipts.create(candidate)
    commands, retries, retry_errors = [], [], []
    original_unknown = service._receipts.unknown

    def failed_uv(command, *_args):
        commands.append(command)
        msg = "The fake installer changed state before failing."
        raise HeinzelError(msg)

    def interleaved_retry(operation_id):
        if not retries:
            retries.append(operation_id)
            try:
                service._confirm_receipt(operation_id, candidate)
            except HeinzelError as error:
                retry_errors.append(str(error))
        original_unknown(operation_id)

    monkeypatch.setattr(installation, "run", failed_uv)
    monkeypatch.setattr(service, "_git_environment", dict)
    monkeypatch.setattr(service._receipts, "unknown", interleaved_retry)
    with pytest.raises(HeinzelError, match="fake installer"):
        service._confirm_receipt(preview.operation_id, candidate)

    assert len(commands) == 1
    assert len(retry_errors) == 1
    assert "already running" in retry_errors[0]
    assert service.operation(preview.operation_id).status == "unknown"
    with pytest.raises(HeinzelError, match="unknown"):
        service._confirm_receipt(preview.operation_id, candidate)
    assert len(commands) == 1


def test_a_dead_owners_receipt_remains_unknown_and_cannot_become_a_fresh_claim(tmp_path, candidate, monkeypatch):
    receipts = PluginReceipts(tmp_path)
    preview = receipts.create(candidate)
    receipts.claim(preview.operation_id)
    monkeypatch.setattr(_receipts, "_alive", lambda _pid: False)

    assert receipts.claim(preview.operation_id).status == "unknown"
    assert receipts.claim(preview.operation_id).status == "unknown"
    assert receipts.get(preview.operation_id).status == "unknown"


def test_terminal_sync_failure_can_reconcile_verified_completion_without_another_claim(
    tmp_path, candidate, monkeypatch
):
    receipts = PluginReceipts(tmp_path)
    preview = receipts.create(candidate)
    receipts.claim(preview.operation_id)
    result = PluginChange(
        action="add", done=True, distribution=candidate.distribution, candidate=candidate,
        plugins=[], restart_required=True, next=[],
    )  # fmt: skip
    original_sync = _receipts._fsync_directory

    def fail_terminal_sync(directory):
        record = json.loads(receipts._path(preview.operation_id).read_text())
        if directory == receipts.directory and record["status"] == "completed":
            raise OSError(errno.EIO, "The final receipt acknowledgment could not be synchronized.")
        original_sync(directory)

    monkeypatch.setattr(_receipts, "_fsync_directory", fail_terminal_sync)
    with pytest.raises(HeinzelError, match="could not be made durable"):
        receipts.complete(preview.operation_id, result)
    receipts.unknown(preview.operation_id)

    reconciled = PluginReceipts(tmp_path).get(preview.operation_id)
    assert reconciled.status == "completed"
    assert reconciled.result == result.model_copy(update={"operation_id": preview.operation_id})
    assert receipts.claim(preview.operation_id) == reconciled
