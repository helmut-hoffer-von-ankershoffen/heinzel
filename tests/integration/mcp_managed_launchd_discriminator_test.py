"""One managed generation against the user launchd of a disposable hosted Mac."""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from heinzel import Config, HeinzelError, Settings
from heinzel.kernel import HostServiceManager, launchd_labels, private_read
from heinzel.kernel._config import config_files
from heinzel.modules.system.mcp import _service
from tests.fixtures.mcp_managed import Installed

pytestmark = pytest.mark.integration
PARENT = "2461d86fa5f70a54f41680393529ab30ee7b8ee2"
NODE = "tests/integration/mcp_managed_launchd_discriminator_test.py::test_disposable_aqua_managed_mcp_closure"
REPOSITORY = "helmut-hoffer-von-ankershoffen/heinzel"
REF = "validate/4pih3-launchd-2461d86"
LAUNCHCTL = "/bin/launchctl"


def _git(*args):
    return subprocess.run(
        ["/usr/bin/git", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
        env={"PATH": "/usr/bin:/bin"},
    ).stdout.strip()


def _hosted():
    if not (
        __debug__
        and sys.platform == "darwin"
        and os.environ.get("CI") == "true"
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_OS") == "macOS"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and os.environ.get("GITHUB_WORKFLOW") == "Focused job"
        and os.environ.get("FOCUSED_KIND") == "pytest"
        and os.environ.get("FOCUSED_REF") == REF
        and os.environ.get("FOCUSED_SELECTOR") == NODE
        and os.environ.get("FOCUSED_REPEAT") == "1"
        and os.environ.get("FOCUSED_STRESS") == "false"
        and os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
        and os.environ.get("GITHUB_REF") == f"refs/heads/{REF}"
        and os.environ.get("GITHUB_WORKFLOW_REF") == f"{REPOSITORY}/.github/workflows/focused-job.yml@refs/heads/{REF}"
    ):
        pytest.skip("Native discriminator requires the designated disposable hosted macOS selection")
    checkout = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    temporary = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    assert Path.cwd().resolve() == checkout
    assert temporary.is_relative_to(checkout.parents[1])
    assert temporary.is_dir()
    assert temporary.stat().st_uid == os.getuid()
    assert os.getuid() != 0
    head = _git("rev-parse", "HEAD")
    assert head == os.environ["GITHUB_SHA"]
    assert _git("rev-parse", "HEAD^") == PARENT
    assert not _git("status", "--porcelain=v1", "--untracked-files=no")
    assert _git("diff", "--name-status", PARENT, "HEAD") == "A\t" + NODE.partition("::")[0]
    return temporary, head, _git("rev-parse", "HEAD^{tree}")


class OneGeneration(HostServiceManager):
    """Allow one product registration and observations of the fixture generation only."""

    def __init__(self, actual, service):
        self.actual = actual
        self.authority = service.authority()
        self.generation = None

    def command(self, arguments):
        if arguments == [LAUNCHCTL, "list"]:
            return self.actual.command(arguments)
        generation = self.authority.existing()
        assert generation is not None
        prefix = self.authority.prefix
        label = generation.label(prefix)
        unit = generation.path(prefix, ".plist")
        uid = os.getuid()
        if arguments == [LAUNCHCTL, "print", f"gui/{uid}/{label}"]:
            return self.actual.command(arguments)
        assert arguments == [LAUNCHCTL, "bootstrap", f"gui/{uid}", str(unit)]
        assert self.generation is None
        assert "LaunchAgents" not in unit.parts
        if private_read(unit) != generation.definition(prefix, self.authority.executable):
            pytest.fail("The disposable definition changed before registration", pytrace=False)
        assert label not in launchd_labels(self.actual)
        self.generation = generation  # also capture uncertain bootstrap before effect
        return self.actual.command(arguments)


def _fixture_kernel(make_kernel, monkeypatch, directory):
    for key in tuple(os.environ):
        if key.startswith("HZ_") or key in {"GITHUB_TOKEN", "GH_TOKEN", "UV_PUBLISH_TOKEN", "XDG_CONFIG_DIRS"}:
            monkeypatch.delenv(key, raising=False)
    private = directory / "home"
    private.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(private))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(private / "site"))
    monkeypatch.setenv("HZ_CONFIG_HOME", str(private / "config"))
    monkeypatch.setenv("HZ_UPDATE_CHECK", "false")
    monkeypatch.setenv("HZ_AUTO_UPDATE", "false")
    monkeypatch.setenv("HZ_PLUGINS_ENABLED", "false")
    monkeypatch.chdir(private)
    (private / "hz.toml").touch(mode=0o600)
    assert all(item.path.is_relative_to(private) for item in config_files(private))
    settings = Settings(
        state_dir=private / "state",
        data_dir=private / "data",
        cache_dir=private / "cache",
        log_dir=private / "logs",
        features=frozenset({"managed_mcp"}),
        plugins_enabled=False,
        update_check=False,
        auto_update=False,
    )
    return make_kernel(builtins=True, settings_=settings, config=Config.empty())


def _observe(service, manager, fixture, report):
    started = service.start()
    assert started.state == "ready"
    assert started.readiness == "authenticated-initialized"
    assert started.tools is not None
    assert started.tools > 0
    assert manager.generation is not None
    assert started.generation == manager.generation.generation
    report["protocol_ready"] = True
    _observe_shutdown(service, manager, fixture, report)
    try:
        stopped = service.stop()
    finally:
        report["native_post_stop"] = _post_stop_shape(manager)
    assert report["authenticated_shutdown_ack"], "Stop did not receive an authenticated shutdown acknowledgment"
    assert stopped.generation == started.generation
    assert stopped.owned_server == "closed"
    assert stopped.descendants == "unverified"
    assert stopped.can_start
    assert _service.port_closed(manager.generation.port)
    report["post_stop"] = stopped.manager_cleanup
    report["loopback_refused"] = True
    shape = report["native_post_stop"]
    assert shape["manager_verified"]
    if shape["listed"]:
        assert shape["print_succeeded"]
        assert shape["target_envelope"]
        assert shape["inactive_state"]
        assert shape["zero_active_count"]
        assert not shape["pid_field"]
        assert (stopped.state, stopped.manager_cleanup) == ("worker-closed", "pending")
        report["registration_disposition"] = "retained-for-vm-teardown"
    else:
        assert not shape["print_succeeded"]
        assert (stopped.state, stopped.manager_cleanup) == ("stopped", "cleared")
        report["registration_disposition"] = "observed-absent-before-teardown"


def _observe_shutdown(service, manager, fixture, report):
    request = service._request

    def observed(authority, generation, *, stop=False):
        if stop:
            assert generation == manager.generation
            assert not report["authenticated_shutdown_ack"]
        result = request(authority, generation, stop=stop)
        if stop:
            assert result == 0
            report["authenticated_shutdown_ack"] = True
        return result

    fixture.setattr(service, "_request", observed)


def _post_stop_shape(manager):
    generation = manager.generation
    assert generation is not None
    label = generation.label(manager.authority.prefix)
    target = f"gui/{os.getuid()}/{label}"
    try:
        observed = manager.actual.command([LAUNCHCTL, "print", target])
        listed = label in launchd_labels(manager.actual)
    except HeinzelError:
        return {"manager_verified": False}
    text = observed.stdout
    return {
        "manager_verified": True,
        "listed": listed,
        "print_succeeded": observed.returncode == 0,
        "target_envelope": text.startswith(f"{target} = {{\n") and text.rstrip().endswith("\n}"),
        "inactive_state": "\n\tstate = not running\n" in text,
        "zero_active_count": "\n\tactive count = 0\n" in text,
        "pid_field": re.search(r"(?m)^[ \t]*pid = ", text) is not None,
    }


def _worker_audits(manager, report):
    if manager.generation is None:
        return
    directory = manager.generation.directory(manager.authority.prefix)
    files = tuple(directory.glob(".fixture-audit-*.json"))
    report["worker_audit_count"] = len(files)
    if len(files) != 2:
        return
    expected = json.dumps({"private": True, "update_check": False, "external": 0, "updates": 0}, sort_keys=True)
    try:
        for path in files:
            if path.is_symlink() or not path.is_file():
                return
            with path.open("rb") as audit:
                data = audit.read(4097)
            if len(data) > 4096:
                return
            value = json.loads(data)
            if json.dumps(value, sort_keys=True) != expected:
                return
    except (OSError, ValueError):
        return
    report["worker_audit_verified"] = True


@pytest.mark.timeout(60)
def test_disposable_aqua_managed_mcp_closure(make_kernel, monkeypatch, record_testsuite_property):
    temporary, head, tree = _hosted()  # before constructing or calling any native manager
    report = {
        "head": head,
        "tree": tree,
        "session_verified": False,
        "registration_disposition": "not-attempted",
        "authenticated_shutdown_ack": False,
        "worker_audit_verified": False,
    }
    try:
        with monkeypatch.context() as fixture:
            # Retain the private fixture directory until hosted runner teardown, even on ambiguity.
            directory = Path(tempfile.mkdtemp(prefix="hz-mcp-launchd-", dir=temporary)).resolve(strict=True)
            installed = Installed(directory)
            fixture.setattr(_service, "installation", lambda: installed)
            kernel = _fixture_kernel(make_kernel, fixture, directory)
            service = _service.Service(kernel)
            actual = service.manager()
            try:
                launchd_labels(actual)  # actual manager adapter checks Aqua and same UID before and after
            except HeinzelError:
                pytest.fail(
                    "The disposable runner cannot verify its Aqua manager; no registration was attempted", pytrace=False
                )
            report["session_verified"] = True
            manager = OneGeneration(actual, service)
            fixture.setattr(service, "manager", lambda: manager)
            try:
                _observe(service, manager, fixture, report)
            finally:
                if manager.generation is not None and report["registration_disposition"] == "not-attempted":
                    report["registration_disposition"] = "unverified-for-vm-teardown"
                _worker_audits(manager, report)
            assert report["worker_audit_verified"], "The private worker audits are incomplete or differ from policy"
    finally:
        record_testsuite_property("mcp_launchd_discriminator", json.dumps(report, sort_keys=True))
