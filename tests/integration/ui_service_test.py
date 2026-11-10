"""Persistent web integration uses isolated files and fake service managers; it never alters the host's jobs."""

import ast
import hashlib
import json
import os
import plistlib
import shlex
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from heinzel import Config, HeinzelError, Settings
from heinzel.heads.cli import build
from heinzel.heads.web._supervision import SupervisedWeb
from heinzel.kernel import (
    PrivateControl,
    host_maintenance_gate,
    private_lock,
    private_read,
    private_socket_directory,
    private_write,
)
from heinzel.modules.system.runner.chat._policy import refusal
from heinzel.modules.ui._service import LABEL, WebService

pytestmark = pytest.mark.integration
TOKEN = "t" * 43
INSTANCE = "a" * 32


class Manager:
    """An isolated manager with distinguishable on-disk and already-loaded service definitions."""

    def __init__(self):
        self.service = None
        self.loaded = False
        self.active = False
        self.foreign = False
        self.fail_load = False
        self.fail_stop = False
        self.calls = []
        self.pid = os.getpid()

    def __call__(self, command):
        self.calls.append(list(command))
        args = command[1:]
        args = args[1:] if args[:1] == ["--user"] else args
        operation = args[0]
        result, code = "", 0
        if operation == "managername":
            result = "Aqua\n"
        elif operation == "manageruid":
            result = f"{os.getuid()}\n"
        elif operation == "list":
            result = "PID\tStatus\tLabel\n" + (f"{self.pid}\t0\t{LABEL}\n" if self.loaded else "")
        elif operation == "print":
            result, code = self._print()
        elif operation in {"bootstrap", "enable"}:
            code = int(self.fail_load)
            self.loaded = self.active = not self.fail_load
        elif operation in {"bootout", "disable"}:
            code = int(self.fail_stop)
            self._stop(operation)
        elif operation == "daemon-reload":
            if not self.service.unit.exists():
                self.loaded = False
        elif operation == "show":
            result = self._show(args)
        return subprocess.CompletedProcess(command, code, result, "")

    def _stop(self, operation):
        if not self.fail_stop:
            self.active = False
            if operation == "bootout":
                self.loaded = False

    def _print(self):
        result, code = "", 0
        if self.loaded:
            plist = plistlib.loads(self.service.unit.read_bytes())
            arguments = plist["ProgramArguments"] + (["--foreign"] if self.foreign else [])
            result = f"path = {self.service.unit}\nprogram = /bin/sh\narguments = {{\n" + "\n".join(arguments) + "\n}\n"
            if self.active:
                result += f"pid = {self.pid}\n"
        else:
            code = 113
        return result, code

    def _show(self, args):
        if args[-1] == "--property=ActiveState,MainPID":
            result = (
                f"ActiveState={'active' if self.active else 'inactive'}\nMainPID={self.pid if self.active else 0}\n"
            )
        elif self.loaded:
            start = f"/bin/sh {self.service.launcher}"
            result = (
                f"LoadState=loaded\nFragmentPath={self.service.unit}\n"
                f"DropInPaths={'foreign.conf' if self.foreign else ''}\n"
                f"ExecStart={{ path=/bin/sh ; argv[]={start} ; ignore_errors=no ; }}\n"
                f"MainPID={self.pid if self.active else 0}\n"
            )
        else:
            result = "LoadState=not-found\nFragmentPath=\nDropInPaths=\nExecStart=\nMainPID=0\n"
        return result


@pytest.fixture
def service_factory(tmp_path):
    def create(platform):
        home = tmp_path.resolve() / "home"
        home.mkdir(mode=0o700)
        executable = home / "bin/hz"
        executable.parent.mkdir(mode=0o700)
        executable.write_text("fixture stable shim")
        manager = Manager()
        service = WebService(
            tmp_path.resolve() / "state",
            home,
            executable,
            platform=platform,
            runner=manager,
            which=lambda _: "/fixture/manager",
            verify=lambda: True,
        )
        manager.service = service
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        return service, manager, port

    return create


@pytest.fixture(params=["darwin", "linux"])
def service(service_factory, request):
    return service_factory(request.param)


def start_control(service, port, *, restart=lambda: None):
    record = json.loads(private_read(service.marker))
    return SupervisedWeb(
        service.directory,
        generation=record["generation"],
        port=port,
        instance=INSTANCE,
        version="1.2.3",
        url=f"http://127.0.0.1:{port}/#token={TOKEN}",
        restart=restart,
    )


def test_install_reports_loaded_separately_from_live_ready_and_never_persists_a_bearer(service):
    service, manager, port = service
    outcome = service.install(port)
    assert outcome.changed
    assert outcome.status.installed
    assert outcome.status.loaded
    assert not outcome.status.ready
    assert TOKEN not in outcome.model_dump_json()
    control = start_control(service, port)
    try:
        status = service.status()
        assert status.ready
        assert status.pid == os.getpid()
        assert status.instance == INSTANCE
        assert status.version == "1.2.3"
        assert TOKEN not in status.model_dump_json()
        assert TOKEN not in service.marker.read_text() + service.runtime.read_text() + service.unit.read_text()
        assert service.open_local() == f"http://127.0.0.1:{port}/#token={TOKEN}"
    finally:
        control.close()
    assert manager.active
    assert not service.status().ready
    assert service.install(port).changed is False


def test_dry_run_and_status_never_create_files_or_load_jobs(service):
    service, manager, port = service
    assert not service.status().installed
    result = service.install(port, dry_run=True)
    assert result.dry_run
    assert not result.changed
    assert not service.marker.exists()
    assert not service.unit.exists()
    assert not service.directory.exists()
    assert not any(call[-1:] == ["bootstrap"] for call in manager.calls)


def test_foreign_listener_is_preserved_without_installing_integration(service):
    service, manager, _ = service
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", 0))
        foreign.listen()
        port = foreign.getsockname()[1]
        with pytest.raises(OSError, match="Address already in use"):
            service.install(port)
        assert not service.unit.exists()
        assert not manager.loaded
        assert foreign.fileno() >= 0


def test_loaded_identity_changes_refuse_restart_and_uninstall(service):
    service, manager, port = service
    service.install(port)
    before = service.unit.read_bytes()
    manager.foreign = True
    assert not service.status().ready
    with pytest.raises(HeinzelError):
        service.restart()
    with pytest.raises(HeinzelError):
        service.uninstall()
    assert service.unit.read_bytes() == before
    assert manager.active


def test_foreign_or_edited_units_are_never_overwritten_or_removed(service):
    service, manager, port = service
    service.unit.parent.mkdir(parents=True)
    service.unit.write_text("foreign unit")
    with pytest.raises(HeinzelError):
        service.install(port)
    with pytest.raises(HeinzelError):
        service.uninstall()
    assert service.unit.read_text() == "foreign unit"
    assert not manager.loaded


def test_uninstall_verifies_stop_and_preserves_data_and_pairing(service):
    service, manager, port = service
    service.install(port)
    paired = service.directory / "devices.json"
    paired.write_text("paired fixture")
    manager.fail_stop = True
    with pytest.raises(HeinzelError):
        service.uninstall()
    assert service.unit.exists()
    assert service.marker.exists()
    manager.fail_stop = False
    assert service.uninstall().changed
    assert not manager.active
    assert not service.unit.exists()
    assert not service.marker.exists()
    assert paired.read_text() == "paired fixture"


def test_failed_load_is_an_error_and_never_claims_readiness(service):
    service, manager, port = service
    manager.fail_load = True
    with pytest.raises(HeinzelError, match="could not be loaded"):
        service.install(port)
    assert not service.status().ready
    assert service.marker.exists()
    assert service.unit.exists()


def test_stale_pid_or_generation_is_not_used_as_a_ready_control_endpoint(service, monkeypatch):
    service, manager, port = service
    service.install(port)
    control = start_control(service, port)
    try:
        manager.pid += 1
        monkeypatch.setattr(PrivateControl, "request", lambda *_: pytest.fail("stale endpoint was contacted"))
        assert not service.status().ready
    finally:
        control.close()


def test_live_restart_uses_private_control_and_preserves_the_service_definition(service):
    service, _, port = service
    service.install(port)
    called = []
    control = start_control(service, port, restart=lambda: called.append(True))
    try:
        before = service.unit.read_bytes()
        service.restart()
        assert called == [True]
        assert service.unit.read_bytes() == before
    finally:
        control.close()


def test_replacement_publication_survives_retiring_instance_cleanup(service):
    service, _, port = service
    service.install(port)
    first = start_control(service, port)
    second = start_control(service, port)
    try:
        name = json.loads(service.runtime.read_text())["socket"]
        first.close()
        assert service.runtime.exists()
        assert (private_socket_directory(service.directory) / name).exists()
        assert service.status().ready
    finally:
        second.close()


def test_symlinked_private_records_and_concurrent_mutations_are_refused(service, tmp_path):
    service, _, port = service
    service.install(port)
    original = service.marker.read_text()
    service.marker.unlink()
    target = tmp_path / "foreign.json"
    target.write_text(original)
    service.marker.symlink_to(target)
    with pytest.raises(HeinzelError):
        service.uninstall()
    assert target.read_text() == original
    service.marker.unlink()
    private_write(service.marker, original)
    with private_lock(service.directory / "lifecycle.lock"), pytest.raises(HeinzelError, match="in progress"):
        service.uninstall()


def test_public_ui_commands_are_cli_only_and_chat_cannot_install_persistence(make_kernel, service):
    service, _, port = service
    configured = Config.empty().settings(Settings, "hz").model_copy(update={"features": frozenset({"persistent_web"})})
    kernel = make_kernel(builtins=True, settings_=configured)
    kernel.bind(WebService, service)
    app = build(kernel)
    result = CliRunner().invoke(app, ["ui", "install", "--port", str(port), "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["dry_run"]
    assert "#token=" not in result.stdout
    for name in ("install", "restart", "uninstall"):
        assert refusal("ui", name, {}) is not None
    assert CliRunner().invoke(app, ["ui", "status", "--json"]).exit_code == 0


def test_external_startup_guard_refuses_interrupted_update_before_any_tool_code_executes(service, tmp_path):
    service, _, port = service
    service.install(port)
    trace = tmp_path / "executed"
    service.executable.write_text(f"#!/bin/sh\nset -eu\nprintf '%s' started > '{trace}'\n")
    service.executable.chmod(0o700)
    backup = service.directory.parent / "auto-update/before-update"
    backup.mkdir(parents=True)
    refused = subprocess.run(["/bin/sh", str(service.launcher)], check=False, capture_output=True, timeout=2)
    assert refused.returncode == 75
    assert not trace.exists()
    assert refused.stdout == refused.stderr == b""
    backup.rmdir()
    backup.symlink_to(tmp_path / "missing-recovery")
    linked = subprocess.run(["/bin/sh", str(service.launcher)], check=False, capture_output=True, timeout=2)
    assert linked.returncode == 75
    assert not trace.exists()
    backup.unlink()
    started = subprocess.run(["/bin/sh", str(service.launcher)], check=False, capture_output=True, timeout=2)
    assert started.returncode == 0
    assert trace.read_text() == "started"


def test_control_socket_rejects_symlinks_and_permissive_directories_without_exposing_a_session(service, tmp_path):
    service, _, port = service
    service.install(port)
    control = start_control(service, port)
    try:
        link = control.control.path.with_name("foreign.sock")
        link.symlink_to(control.control.path)
        with pytest.raises(HeinzelError, match="owned and private"):
            PrivateControl.request(link, "open")
        link.unlink()
        control.control.path.parent.chmod(0o755)
        with pytest.raises(HeinzelError, match="mode 0700"):
            service.open_local()
    finally:
        control.control.path.parent.chmod(0o700)
        control.close()


def test_runtime_record_does_not_allow_an_empty_instance_or_unrelated_control_path(service):
    service, _, port = service
    service.install(port)
    control = start_control(service, port)
    try:
        record = json.loads(service.runtime.read_text())
        record["instance"] = ""
        private_write(service.runtime, json.dumps(record))
        assert not service.status().ready
        record["instance"] = INSTANCE
        record["socket"] = "../foreign.sock"
        private_write(service.runtime, json.dumps(record))
        assert not service.status().ready
    finally:
        control.close()


def test_never_enabled_uninstall_does_not_contact_an_unavailable_or_foreign_manager(service):
    service, manager, _ = service
    manager.calls.clear()
    manager.loaded = True  # An unrelated same-label job is not ours without integration evidence.
    service.runner = lambda _: pytest.fail("Never-enabled uninstall contacted the service manager")
    result = service.uninstall()
    assert not result.changed
    assert not result.status.installed
    assert manager.calls == []
    assert manager.loaded


def test_dangling_integration_links_do_not_count_as_never_enabled(service, tmp_path):
    service, _, _ = service
    service.directory.mkdir(parents=True, mode=0o700)
    service.marker.symlink_to(tmp_path / "missing-marker")
    service.runner = lambda _: pytest.fail("An invalid record must not mutate a manager")
    with pytest.raises(HeinzelError, match="unowned or changed"):
        service.uninstall()
    assert service.marker.is_symlink()


def test_foreground_ui_with_no_owned_integration_does_not_require_uv(make_kernel, monkeypatch):
    from heinzel.heads import web
    from heinzel.modules.ui import _service

    monkeypatch.setattr(_service.shutil, "which", lambda _, path=None: None)
    opened = []
    monkeypatch.setattr(web, "serve", lambda kernel, **kwargs: opened.append(kwargs))
    result = CliRunner().invoke(build(make_kernel(builtins=True)), ["ui", "serve", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert len(opened) == 1


@pytest.mark.parametrize(
    "gate",
    [
        "auto-update/before-update",
        "daemon/mutation-pending.json",
        "daemon/standard-install-pending.json",
        ".heinzel-startup-pending",
    ],
)
@pytest.mark.parametrize("dangling", [False, True])
def test_every_durable_mutation_gate_refuses_cold_exec_and_private_restart(service, tmp_path, gate, dangling):
    service, _, port = service
    service.install(port)
    calls = []
    control = start_control(service, port, restart=lambda: calls.append(True))
    trace = tmp_path / "tool-executed"
    service.executable.write_text(f"#!/bin/sh\nprintf '%s' executed > '{trace}'\n")
    service.executable.chmod(0o700)
    pending = (service.prefix if gate.startswith(".") else service.directory.parent) / gate
    pending.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dangling:
            pending.symlink_to(tmp_path / "nonexistent-target")
        else:
            pending.write_text("pending")
        refused = subprocess.run(["/bin/sh", str(service.launcher)], check=False, capture_output=True, timeout=2)
        assert refused.returncode == 75
        assert refused.stdout == refused.stderr == b""
        assert not trace.exists()
        with pytest.raises(HeinzelError, match="requires completion or recovery"):
            service.restart()
        response = PrivateControl.request(control.control.path, "restart")
        assert response["error"] == "mutation-pending"
        assert calls == []
    finally:
        pending.unlink()
        control.close()


def test_launcher_pins_guarded_state_and_verified_uv_path_with_minimal_inherited_path(service, tmp_path):
    service, _, port = service
    custom = tmp_path / "custom uv"
    custom.mkdir()
    uv = custom / "uv"
    uv.symlink_to("/usr/bin/printf")
    service.uv = uv
    service.install(port)
    trace = service.directory.parent / "runtime-facts"
    service.executable.write_text(
        "#!/bin/sh\nset -eu\n"
        'printf "%s\\n" "$HZ_STATE_DIR" > "$HZ_STATE_DIR/runtime-facts"\n'
        'uv "%s\\n" fixture-uv >> "$HZ_STATE_DIR/runtime-facts"\n'
    )
    service.executable.chmod(0o700)
    result = subprocess.run(
        ["/bin/sh", str(service.launcher)],
        env={"PATH": "/usr/bin:/bin", "HZ_STATE_DIR": str(tmp_path / "foreign-state")},
        check=False,
        capture_output=True,
        timeout=2,
    )
    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [str(service.directory.parent), "fixture-uv"]


def test_user_service_factory_captures_verified_uv_and_tool_prefix_without_loading_a_job(
    service, make_kernel, monkeypatch
):
    from heinzel.kernel import Installation
    from heinzel.modules.ui import _service

    service, manager, _ = service
    expected = service.prefix / "bin/hz"
    expected.parent.mkdir(parents=True)
    expected.write_text("verified tool fixture")
    service.executable.unlink()
    service.executable.symlink_to(expected)
    monkeypatch.setenv("HZ_STATE_DIR", str(service.directory.parent))
    monkeypatch.setattr(_service.shutil, "which", lambda name, path=None: "/poison/uv" if name == "uv" else None)
    monkeypatch.setattr(_service, "_run", lambda _: pytest.fail("No ambient subprocess"))
    verified = []
    monkeypatch.setattr(
        _service, "verified_installed_path", lambda prefix: verified.append(prefix) or prefix / "bin/uv"
    )
    kernel = make_kernel(builtins=True)
    kernel.bind(
        Installation,
        SimpleNamespace(
            prefix=service.prefix,
            is_tool=True,
            startup_state_matches=lambda: True,
            clear_stale_startup_pending=lambda: False,
            tool_destination=lambda: (service.prefix.parent, service.executable.parent),
        ),
    )
    created = WebService.for_user(kernel)
    assert created.repair is kernel.resolve(Installation).clear_stale_startup_pending
    assert created.prefix == service.prefix
    assert created.uv == service.prefix / "bin/uv"
    assert created.which("launchctl") == "/bin/launchctl"
    assert created.which("systemctl") == "/usr/bin/systemctl"
    assert verified == []
    assert created.verify()
    assert verified == [service.prefix]
    assert manager.calls == []


def test_owned_service_home_below_root_sticky_tmp_is_supported_without_adopting_foreign_entries(service):
    service, manager, port = service
    sticky = Path("/tmp").resolve()  # noqa: S108 - a securely owned temporary child exercises this root sticky ancestor
    with tempfile.TemporaryDirectory(prefix="hz-ui-home-", dir=sticky) as directory:
        isolated = WebService(
            service.directory.parent,
            Path(directory),
            service.executable,
            platform=service.platform,
            runner=manager,
            which=service.which,
            verify=lambda: True,
            prefix=service.prefix,
            uv=service.uv,
        )
        manager.service = isolated
        result = isolated.install(port)
        assert result.changed
        assert result.status.installed
        assert result.status.loaded
        assert isolated.unit.is_file()


def test_default_install_keeps_inspection_and_owned_removal_without_a_preview(make_kernel, service):
    service, manager, port = service
    kernel = make_kernel(builtins=True)
    kernel.bind(WebService, service)
    app = build(kernel)
    installed = CliRunner().invoke(app, ["ui", "install", "--port", str(port), "--json"])
    assert installed.exit_code == 0, installed.output
    assert json.loads(installed.stdout)["status"]["installed"] is True
    assert manager.loaded
    assert CliRunner().invoke(app, ["ui", "status", "--json"]).exit_code == 0
    removed = CliRunner().invoke(app, ["ui", "uninstall", "--json"])
    assert removed.exit_code == 0
    assert json.loads(removed.stdout)["changed"]
    assert not service.marker.exists()


def test_dangling_attachment_record_never_falls_back_to_a_foreground_server(
    make_kernel, service, monkeypatch, tmp_path
):
    from heinzel.heads import web

    service, _, _ = service
    service.directory.mkdir(parents=True, mode=0o700)
    service.marker.symlink_to(tmp_path / "absent-service-record")
    monkeypatch.setenv("HZ_STATE_DIR", str(service.directory.parent))
    monkeypatch.setenv("HZ_FEATURES", "persistent_web")
    monkeypatch.setattr(web, "serve", lambda *_args, **_kwargs: pytest.fail("Unsafe foreground fallback"))
    kernel = make_kernel(builtins=True)
    kernel.bind(WebService, service)
    result = CliRunner().invoke(build(kernel), ["ui", "open", "--no-browser"])
    assert result.exit_code == 1
    assert "not ready" in result.output
    assert service.marker.is_symlink()


def test_default_attachment_uses_existing_private_service_instead_of_starting_a_competitor(
    make_kernel, service, monkeypatch
):
    from heinzel.heads import web

    service, _, port = service
    service.install(port)
    monkeypatch.setenv("HZ_STATE_DIR", str(service.directory.parent))
    monkeypatch.setattr(web, "serve", lambda *_args, **_kwargs: pytest.fail("Unsafe foreground fallback"))
    kernel = make_kernel(builtins=True)
    kernel.bind(WebService, service)
    control = start_control(service, port)
    try:
        result = CliRunner().invoke(build(kernel), ["ui", "open", "--no-browser"])
        assert result.exit_code == 0, result.output
        assert result.stdout == f"http://127.0.0.1:{port}/#token={TOKEN}\n"
    finally:
        control.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_supervised_entry_keeps_its_generation_without_a_preview(make_kernel, monkeypatch, legacy):
    from heinzel.heads import web

    calls = []
    monkeypatch.setattr(web, "serve", lambda *_args, **kwargs: calls.append(kwargs))
    command = ["ui", *([] if legacy else ["serve"]), "--no-browser", "--port", "4321", "--supervised", "a" * 32]
    result = CliRunner().invoke(build(make_kernel(builtins=True)), command)
    assert result.exit_code == 0, result.output
    assert calls[0]["supervised"] == "a" * 32
    assert calls[0]["port"] == 4321
    assert calls[0]["browser"] is False


def assert_loaded_identity_refused(service, manager, port, alteration):
    service.install(port)
    calls = []
    control = start_control(service, port, restart=lambda: calls.append(True))
    assert service.status().ready
    before = {path: path.read_bytes() for path in (service.unit, service.marker, service.launcher)}
    original = service.runner
    manager.calls.clear()

    def changed(command):
        result = original(command)
        operation = command[1] if service.platform == "darwin" else command[2]
        if operation == ("print" if service.platform == "darwin" else "show"):
            output = result.stdout
            if service.platform == "darwin":
                if alteration == "fragment":
                    output = output.replace(f"path = {service.unit}", "path = /foreign/job.plist", 1)
                elif alteration == "program":
                    output = output.replace("program = /bin/sh", "program = /foreign/program", 1)
                else:
                    output = output.replace(f"{service.launcher}\n}}", f"{service.launcher}\n--unexpected\n}}", 1)
            elif alteration == "fragment":
                output = output.replace(f"FragmentPath={service.unit}", "FragmentPath=/foreign/job.service", 1)
            elif alteration == "program":
                output = output.replace("path=/bin/sh ;", "path=/foreign/program ;", 1)
            elif alteration == "argv":
                output = output.replace(
                    f"argv[]=/bin/sh {service.launcher} ;", f"argv[]=/bin/sh {service.launcher} --unexpected ;", 1
                )
            else:
                output = output.replace("\nMainPID=", " { path=/bin/sh ; argv[]=/bin/sh /foreign ; }\nMainPID=", 1)
            return subprocess.CompletedProcess(command, result.returncode, output, result.stderr)
        return result

    service.runner = changed
    try:
        assert not service.status().ready
        with pytest.raises(HeinzelError):
            service.restart()
        with pytest.raises(HeinzelError):
            service.uninstall()
        assert calls == []
        assert manager.active
        assert not any("bootout" in command or "disable" in command for command in manager.calls)
        assert {path: path.read_bytes() for path in before} == before
    finally:
        control.close()


@pytest.mark.parametrize("alteration", ["fragment", "program", "argv"])
def test_each_loaded_identity_field_independently_blocks_mutation_with_a_live_control(service, alteration):
    service, manager, port = service
    assert_loaded_identity_refused(service, manager, port, alteration)


def test_linux_second_exec_vector_is_refused_even_when_first_vector_matches(service_factory):
    service, manager, port = service_factory("linux")
    assert_loaded_identity_refused(service, manager, port, "second-vector")


def test_profile_binding_survives_minimal_and_conflicting_manager_environment(service, tmp_path):
    from heinzel.modules.ui._profile import default_profile

    selected, manager, port = service
    config = tmp_path / "selected-config/config.toml"
    config.parent.mkdir(mode=0o700)
    config.write_text('[hz]\nfeatures = ["persistent_web"]\nauto_update = false\n[system.notify]\nnative = false\n')
    selected.profile = default_profile(selected.home, selected.directory.parent, selected.prefix, selected.executable)
    selected.profile.update({"HZ_CONFIG_HOME": str(config.parent), "HZ_CONFIG": str(config)})
    output = tmp_path / "profile-observation.json"
    script = (
        "import json,os; from pathlib import Path; from heinzel import Config,Settings; "
        "c=Config.load(Path.home()); s=c.settings(Settings,'hz'); "
        "result={'home':str(Path.home()),'features':sorted(s.features),'auto_update':s.auto_update,"
        "'roots':[str(s.state_dir),str(s.data_dir),str(s.cache_dir),str(s.log_dir)],"
        "'secret_absent':'OPENAI_API_KEY' not in os.environ,"
        "'python_absent':'PYTHONPATH' not in os.environ,"
        "'session_retained':os.environ.get('DBUS_SESSION_BUS_ADDRESS')=='fixture-session'}; "
        f"Path({str(output)!r}).write_text(json.dumps(result))"
    )
    selected.executable.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, "-I", "-c", script]) + "\n")
    selected.executable.chmod(0o700)
    selected.install(port)
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "foreign-home"),
        "HZ_FEATURES": "foreign",
        "HZ_CONFIG": str(tmp_path / "foreign-config"),
        "HZ_DATA_DIR": str(tmp_path / "foreign-data"),
        "PYTHONPATH": "foreign-python",
        "OPENAI_API_KEY": "fixture-secret-never-persist",
        "DBUS_SESSION_BUS_ADDRESS": "fixture-session",
    }
    subprocess.run(["/bin/sh", str(selected.launcher)], env=environment, check=True, capture_output=True, timeout=5)
    observed = json.loads(output.read_text())
    assert observed["home"] == str(selected.home)
    assert observed["features"] == ["persistent_web"]
    assert observed["auto_update"] is False
    assert observed["roots"] == [
        selected.profile[name] for name in ("HZ_STATE_DIR", "HZ_DATA_DIR", "HZ_CACHE_DIR", "HZ_LOG_DIR")
    ]
    assert observed["secret_absent"] is True
    assert observed["python_absent"] is True
    assert observed["session_retained"] is True
    assert not (tmp_path / "foreign-home").exists()
    assert not (tmp_path / "foreign-data").exists()
    for path in (selected.marker, selected.launcher, selected.unit):
        assert "fixture-secret-never-persist" not in path.read_text()
        assert "fixture-session" not in path.read_text()
    config.write_text("[hz]\nfeatures = []\nauto_update = true\n")
    subprocess.run(["/bin/sh", str(selected.launcher)], env=environment, check=True, capture_output=True, timeout=5)
    changed = json.loads(output.read_text())
    assert changed["features"] == []
    assert changed["auto_update"] is True
    selected.profile = {**selected.profile, "HZ_CONFIG_HOME": str(tmp_path / "other-caller")}
    selected.unsupported = ("HZ_FEATURES",)
    assert selected.status().installed
    assert selected.uninstall().changed
    assert not manager.loaded


def test_process_only_behavior_is_refused_before_install_without_exposing_values(service, monkeypatch):
    from heinzel import Config
    from heinzel.modules.ui._profile import installation_overrides

    selected, manager, port = service
    monkeypatch.setenv("HZ_FEATURES", "fixture-secret-never-persist")
    selected.unsupported = installation_overrides(Config.load(selected.home))
    with pytest.raises(HeinzelError, match="selected user configuration") as error:
        selected.install(port)
    assert "HZ_FEATURES" in str(error.value)
    assert "fixture-secret-never-persist" not in str(error.value)
    assert manager.calls == []
    assert not selected.unit.exists()


@pytest.mark.parametrize("damage", ["missing", "unknown", "relative", "malformed"])
def test_malformed_recorded_profile_cannot_authorize_uninstall(service, damage):
    selected, manager, port = service
    selected.install(port)
    record = json.loads(private_read(selected.marker))
    if damage == "missing":
        del record["profile"]
    elif damage == "unknown":
        record["profile"]["OPENAI_API_KEY"] = "fixture-secret"
    elif damage == "relative":
        record["profile"]["HZ_DATA_DIR"] = "relative"
    else:
        record["profile"] = False
    private_write(selected.marker, json.dumps(record))
    manager.calls.clear()
    with pytest.raises(HeinzelError):
        selected.uninstall()
    assert manager.loaded
    assert not any("bootout" in call or "disable" in call for call in manager.calls)


def test_real_user_factory_binds_profile_and_refuses_process_behavior_before_manager_calls(
    service, make_kernel, monkeypatch, tmp_path
):
    from heinzel.kernel import Installation
    from heinzel.modules.ui import _service

    selected, manager, port = service
    for name in list(os.environ):
        if name.upper().startswith("HZ_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(selected.home))
    config_dir = tmp_path / "real-user-config"
    config_dir.mkdir()
    explicit = tmp_path / "explicit-profile.toml"
    explicit.write_text('[hz]\nfeatures = ["persistent_web"]\n')
    environment = {
        "HZ_CONFIG_HOME": str(config_dir),
        "HZ_CONFIG": str(explicit),
        "HZ_STATE_DIR": str(selected.directory.parent),
        "HZ_DATA_DIR": str(tmp_path / "selected-data"),
        "HZ_CACHE_DIR": str(tmp_path / "selected-cache"),
        "HZ_LOG_DIR": str(tmp_path / "selected-log"),
        "UV_CACHE_DIR": str(tmp_path / "selected-uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(tmp_path / "selected-pythons"),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    expected = selected.prefix / "bin/hz"
    expected.parent.mkdir(parents=True)
    expected.write_text("verified fixture")
    selected.executable.unlink()
    selected.executable.symlink_to(expected)
    custom_uv = selected.home / "uv/uv"
    monkeypatch.setattr(_service.shutil, "which", lambda name, path=None: str(custom_uv) if name == "uv" else None)
    monkeypatch.setattr(
        _service, "_run", lambda args: subprocess.CompletedProcess(args, 0, str(selected.executable.parent), "")
    )
    kernel = make_kernel(builtins=True, settings_=Settings(features={"persistent_web"}))
    kernel.bind(
        Installation,
        SimpleNamespace(
            prefix=selected.prefix,
            is_tool=True,
            startup_state_matches=lambda: True,
            clear_stale_startup_pending=lambda: False,
            tool_destination=lambda: (selected.prefix.parent, selected.executable.parent),
        ),
    )
    created = WebService.for_user(kernel)
    assert created.profile == {
        **environment,
        "HOME": str(selected.home),
        "UV_TOOL_DIR": str(selected.prefix.parent),
        "UV_TOOL_BIN_DIR": str(selected.executable.parent),
        "XDG_CONFIG_DIRS": os.environ.get("XDG_CONFIG_DIRS", "/etc/xdg"),
    }
    assert created.unsupported == ()
    created.runner, created.which = manager, selected.which
    manager.service = created
    for name in ("hz_features", "Hz_Auto_Update", "HZ_LOG_LEVEL"):
        monkeypatch.setenv(
            name,
            "false"
            if name == "Hz_Auto_Update"
            else "ERROR"
            if name == "HZ_LOG_LEVEL"
            else "fixture-value-never-expose",
        )
    rejected = WebService.for_user(kernel)
    rejected.runner, rejected.which = manager, selected.which
    assert set(rejected.unsupported) == {"hz_features", "Hz_Auto_Update", "HZ_LOG_LEVEL"}
    with pytest.raises(HeinzelError, match="selected user configuration") as error:
        rejected.install(port)
    assert "fixture-value-never-expose" not in str(error.value)
    assert manager.calls == []
    for name in ("hz_features", "Hz_Auto_Update", "HZ_LOG_LEVEL"):
        monkeypatch.delenv(name)
    explicit.write_text("[hz]\nfeatures = []\n")
    disabled = WebService.for_user(kernel)
    disabled.runner, disabled.which = manager, selected.which
    preview = disabled.install(port, dry_run=True)
    assert preview.dry_run
    assert preview.files
    assert not any(command[1] in {"bootstrap", "enable"} for command in manager.calls)
    assert disabled.status().installed is False
    assert disabled.uninstall().changed is False


class StopClock:
    """Advance stop observation without real sleeping or manipulating OS processes."""

    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def asynchronous_stop(service, manager, clock, *, outcome):
    """A successful stop request precedes the manager's observed terminal state."""
    stopping = False

    def runner(command):
        nonlocal stopping
        args = command[1:]
        args = args[1:] if args[:1] == ["--user"] else args
        operation = args[0]
        if stopping and clock.now >= 0.05:
            if outcome == "settled":
                manager.active = False
                if service.platform == "darwin":
                    manager.loaded = False
            elif outcome == "foreign":
                manager.foreign = True
            elif outcome == "unknown" and operation in {"print", "list", "show"}:
                manager.calls.append(list(command))
                return subprocess.CompletedProcess(command, 1, "", "")
            elif outcome == "changed":
                service.launcher.write_text("changed fixture launcher")
        result = manager(command)
        if operation in {"bootout", "disable"} and result.returncode == 0:
            stopping = True
            manager.active = manager.loaded = True
        return result

    service.runner = runner
    service._clock = clock.clock
    service._sleep = clock.sleep


def test_successful_stop_waits_for_owned_manager_transition_before_removing_files(service):
    selected, manager, port = service
    selected.install(port)
    clock = StopClock()
    asynchronous_stop(selected, manager, clock, outcome="settled")
    assert selected.uninstall().changed
    assert clock.now > 0
    assert not selected.unit.exists()
    assert not selected.marker.exists()
    assert not selected.launcher.exists()
    assert not manager.loaded
    stops = [command for command in manager.calls if "bootout" in command or "disable" in command]
    assert len(stops) == 1


@pytest.mark.parametrize("outcome", ["persistent", "foreign", "unknown", "changed"])
def test_stop_wait_retains_integration_on_timeout_or_lost_ownership(service, outcome):
    selected, manager, port = service
    selected.install(port)
    clock = StopClock()
    asynchronous_stop(selected, manager, clock, outcome=outcome)
    with pytest.raises(HeinzelError):
        selected.uninstall()
    assert selected.unit.exists()
    assert selected.marker.exists()
    assert selected.launcher.exists()
    stops = [command for command in manager.calls if "bootout" in command or "disable" in command]
    assert len(stops) == 1
    if outcome == "persistent":
        assert clock.now == 5.0
    elif outcome == "changed":
        assert clock.now < 5.0
    else:
        assert clock.now == 0.05


@pytest.mark.parametrize(
    ("returncode", "output"),
    [
        (1, "ActiveState=active\nMainPID=7\n"),
        (0, "ActiveState=deactivating\n"),
        (0, "ActiveState=active\nMainPID:7\n"),
        (0, "ActiveState=active\nWrongPID=7\n"),
        (0, "ActiveState=active\nMainPID=7\nUnexpected=extra\n"),
        (0, "ActiveState=active\nMainPID=not-a-pid\n"),
        (0, "ActiveState=active\nMainPID=\u0661\n"),
        (0, "ActiveState=inactive\nMainPID=7\n"),
        (0, "ActiveState=failed\nMainPID=0\n"),
    ],
)
def test_linux_unknown_stop_state_fails_immediately_and_retains_all_integration(service_factory, returncode, output):
    selected, manager, port = service_factory("linux")
    selected.install(port)
    before = {path: path.read_bytes() for path in (selected.marker, selected.unit, selected.launcher)}
    clock = StopClock()
    selected._clock, selected._sleep = clock.clock, clock.sleep

    def runner(command):
        if command[-1] == "--property=ActiveState,MainPID":
            manager.calls.append(list(command))
            return subprocess.CompletedProcess(command, returncode, output, "")
        result = manager(command)
        if "disable" in command:
            manager.active = True
        return result

    selected.runner = runner
    with pytest.raises(HeinzelError, match="stop could not be verified"):
        selected.uninstall()
    assert {path: path.read_bytes() for path in before} == before
    assert clock.now == 0.0
    stops = [command for command in manager.calls if "disable" in command]
    assert len(stops) == 1


def released_launcher(service, port, record, version):
    """Run frozen publisher/git-derived renderer sources, independent of the current production generator."""
    name = "published015" if version == "published015" else "main505"
    path = Path(__file__).parents[1] / "fixtures/web-launcher-history" / f"{name}.json"
    digests = {
        "published015": "1946fc63448a85038859e23d1df5c3d170ef418ea9fbdfb810e37c60c4a74408",
        "main505": "e11673d252ec65b544c60c0204580665c5a8378710fcf2b94ee798ecc19d3397",
    }
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digests[name]
    source = json.loads(path.read_text())
    namespace = {"Path": Path, "shlex": shlex, "plistlib": plistlib, "LABEL": LABEL}
    tree = ast.parse("\n\n".join(source[key] for key in ("session", "gates", "environment", "launcher", "unit")))
    exec(compile(tree, str(path), "exec"), namespace)  # noqa: S102 - execute only SHA256-pinned historical renderer fixtures
    assert namespace["_content"](service) == service.unit.read_text()
    render = namespace["_launch_content"]
    args = (service, port, record["generation"], record["profile"])
    return render(*args) if name == "published015" else render(*args, legacy=version == "main505-legacy")


@pytest.mark.parametrize("version", ["published015", "main505-canonical", "main505-legacy"])
def test_released_launcher_remains_owned_but_changed_legacy_bytes_are_refused(service, version):
    service, manager, port = service
    service.install(port)
    record = json.loads(service.marker.read_text())
    old = released_launcher(service, port, record, version)
    assert " ui serve --no-browser " in record["launcher"]
    record["launcher"] = old
    private_write(service.launcher, old)
    private_write(service.marker, json.dumps(record))
    assert service.status().installed
    assert service.status().loaded
    private_write(service.launcher, old + "# changed\n")
    with pytest.raises(HeinzelError):
        service.uninstall()
    assert manager.loaded


HISTORICAL_VERSIONS = ["published015", "main505-canonical", "main505-legacy"]


def seed_released_service(service, version, *, previous_uv=False):
    selected, manager, port = service
    selected.install(port)
    record = json.loads(selected.marker.read_text())
    current_uv = selected.uv
    if previous_uv:
        selected.uv = selected.home / "previous-uv/uv"
        record["uv"] = str(selected.uv)
    record["launcher"] = released_launcher(selected, port, record, version)
    selected.uv = current_uv
    private_write(selected.launcher, record["launcher"])
    private_write(selected.marker, json.dumps(record))
    manager.calls.clear()
    return selected, manager, port, record


def manager_effects(manager):
    return [
        call
        for call in manager.calls
        if any(part in call for part in ("bootout", "disable", "bootstrap", "enable", "daemon-reload"))
    ]


@pytest.mark.parametrize("version", HISTORICAL_VERSIONS)
def test_explicit_install_migrates_actual_historical_tuple_and_both_shell_fences(service, version):
    selected, manager, port, old = seed_released_service(service, version, previous_uv=True)
    compatibility = selected.compatibility_gate()
    private_write(compatibility, "fixture fence")
    cold = subprocess.run(["/bin/sh", str(selected.launcher)], env={}, capture_output=True, check=False, timeout=5)
    assert cold.returncode == 75
    compatibility.unlink()
    assert not selected.status().ready
    before_unit = selected.unit.read_bytes()
    assert selected.install(port).changed
    new = json.loads(selected.marker.read_text())
    assert not selected.historical_record(new)
    assert new["uv"] == str(selected.uv)
    assert new["profile"] == old["profile"]
    assert new["generation"] != old["generation"]
    assert selected.unit.read_bytes() == before_unit
    assert len([call for call in manager.calls if "bootout" in call or "disable" in call]) >= 1
    assert not compatibility.exists()
    assert not host_maintenance_gate(selected.prefix).exists()
    assert list((selected.directory / "launcher-history").glob("*.json"))
    private_write(host_maintenance_gate(selected.prefix), "fixture SDK fence")
    cold = subprocess.run(["/bin/sh", str(selected.launcher)], env={}, capture_output=True, check=False, timeout=5)
    assert cold.returncode == 75


@pytest.mark.parametrize("version", HISTORICAL_VERSIONS)
def test_explicit_restart_migrates_before_requesting_any_old_runtime_restart(service, version):
    selected, manager, _port, old = seed_released_service(service, version)
    control = start_control(selected, int(old["port"]), restart=lambda: pytest.fail("The old runtime must not restart"))
    try:
        result = selected.restart()
        assert result.requested
        assert result.retiring_instance == INSTANCE
        assert not selected.historical_record(json.loads(selected.marker.read_text()))
        assert manager_effects(manager)
    finally:
        control.close()


@pytest.mark.parametrize("boundary", ["stopped", "launcher-replaced", "loaded", "compatibility-cleared"])
def test_interrupted_historical_migration_keeps_exact_fences_and_can_resume(service, monkeypatch, boundary):
    from heinzel.modules.ui._launcher_transition import LauncherTransition

    selected, manager, port, _old = seed_released_service(service, "published015")
    original_phase, original_replace, original_unlink = LauncherTransition.phase, Path.replace, Path.unlink

    def phase(transition, value):
        original_phase(transition, value)
        if value == boundary:
            msg = "fixture interruption"
            raise OSError(msg)

    def replace(path, target):
        result = original_replace(path, target)
        if boundary == "launcher-replaced" and path.name.endswith("-launcher.new"):
            msg = "fixture interruption"
            raise OSError(msg)
        return result

    def unlink(path, *args, **kwargs):
        result = original_unlink(path, *args, **kwargs)
        if boundary == "compatibility-cleared" and path == selected.compatibility_gate():
            msg = "fixture interruption"
            raise OSError(msg)
        return result

    monkeypatch.setattr(LauncherTransition, "phase", phase)
    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(HeinzelError, match="incomplete"):
        selected.install(port)
    assert host_maintenance_gate(selected.prefix).exists()
    assert selected.unit.exists()
    effects_before = manager_effects(manager)
    monkeypatch.setattr(LauncherTransition, "phase", original_phase)
    monkeypatch.setattr(Path, "replace", original_replace)
    monkeypatch.setattr(Path, "unlink", original_unlink)
    assert selected.install(port).changed
    assert not selected.historical_record(json.loads(selected.marker.read_text()))
    assert not host_maintenance_gate(selected.prefix).exists()
    assert not selected.compatibility_gate().exists()
    if boundary == "compatibility-cleared":
        assert manager_effects(manager) == effects_before


@pytest.mark.parametrize("change", ["foreign", "missing", "edited-launcher", "edited-journal"])
def test_interrupted_historical_transition_refuses_changed_authority_before_new_effects(service, monkeypatch, change):
    from heinzel.modules.ui._launcher_transition import LauncherTransition

    selected, manager, port, _old = seed_released_service(service, "main505-canonical")
    original = LauncherTransition.phase

    def stop_at_phase(transition, value):
        original(transition, value)
        if value == "stopped":
            msg = "fixture interruption"
            raise OSError(msg)

    monkeypatch.setattr(LauncherTransition, "phase", stop_at_phase)
    with pytest.raises(HeinzelError, match="incomplete"):
        selected.install(port)
    monkeypatch.setattr(LauncherTransition, "phase", original)
    if change == "foreign":
        private_write(selected.compatibility_gate(), "foreign operation")
    elif change == "missing":
        selected.compatibility_gate().unlink()
    elif change == "edited-launcher":
        private_write(selected.launcher, selected.launcher.read_text() + "# changed\n")
    else:
        path = selected.directory / "launcher-transition.json"
        path.write_text(path.read_text() + " ")
    before = manager_effects(manager)
    with pytest.raises((HeinzelError, ValueError, OSError)):
        selected.install(port)
    assert manager_effects(manager) == before
    assert selected.unit.exists()
    assert host_maintenance_gate(selected.prefix).exists()
    if change == "foreign":
        assert selected.compatibility_gate().read_text() == "foreign operation"
    if change == "missing":
        assert not selected.compatibility_gate().exists()


@pytest.mark.parametrize("version", HISTORICAL_VERSIONS)
def test_explicit_uninstall_retires_historical_launcher_under_compatibility_fence(service, version):
    selected, manager, _port, _old = seed_released_service(service, version)
    retained = selected.directory / "retained-data"
    retained.write_text("pairing/consent retained")
    assert selected.uninstall().changed
    assert not selected.unit.exists()
    assert not selected.marker.exists()
    assert not selected.launcher.exists()
    assert retained.read_text() == "pairing/consent retained"
    assert not host_maintenance_gate(selected.prefix).exists()
    assert not selected.compatibility_gate().exists()
    assert not manager.active


@pytest.mark.parametrize("value", [None, "uv", "/fixture/uv", "/fixture/\x00uv", "/" + "a" * 4096])
def test_removal_uses_only_a_bounded_absolute_historical_uv_spelling(service, value):
    selected, _manager, _port = service
    state = selected.directory.parent
    assert selected.removal_uv_hint(state) == Path("/usr/bin/uv")
    private_write(selected.marker, json.dumps({"uv": value}))
    if value == "/fixture/uv":
        assert selected.removal_uv_hint(state) == Path(value)
    else:
        with pytest.raises(HeinzelError, match="installation identity changed"):
            selected.removal_uv_hint(state)


def test_historical_service_dry_run_previews_migration_without_touching_manager(service):
    selected, manager, port, _old = seed_released_service(service, "published015")
    launcher = selected.launcher.read_bytes()
    preview = selected.install(port, dry_run=True)
    assert preview.dry_run
    assert not preview.changed
    assert preview.files[str(selected.launcher)].encode() != launcher
    assert selected.launcher.read_bytes() == launcher
    assert not manager_effects(manager)


@pytest.mark.parametrize("failure", ["unverified", "wrong port", "pending"])
def test_historical_migration_refuses_before_any_manager_change(service, failure):
    selected, manager, port, old = seed_released_service(service, "published015")
    if failure == "unverified":
        selected.verify = lambda: False
        message = "Verify the selected standard Heinzel SDK"
    elif failure == "wrong port":
        port = port + 1 if port < 65535 else port - 1
        message = "recorded port"
    else:
        private_write(selected.compatibility_gate(), "fixture recovery fence")
        message = "existing installation change needs recovery"
    with pytest.raises(HeinzelError, match=message):
        selected.install(port)
    assert json.loads(private_read(selected.marker)) == old
    assert not manager_effects(manager)


def test_historical_service_without_a_live_pid_requires_explicit_migration(service):
    selected, manager, _port, _old = seed_released_service(service, "published015")
    manager.active = False
    with pytest.raises(HeinzelError, match="before opening"):
        selected.open_local()
    with pytest.raises(HeinzelError, match="No owned running historical service"):
        selected.restart()
    assert not manager_effects(manager)


def test_open_and_restart_refuse_unavailable_or_mismatched_live_service(service, monkeypatch):
    selected, manager, port = service
    assert selected.open_local() is None
    selected.install(port)
    manager.active = False
    with pytest.raises(HeinzelError, match="not ready"):
        selected.open_local()
    manager.active = True
    control = start_control(selected, port)
    try:
        request = PrivateControl.request

        def response(path, command):
            result = request(path, "status")
            if command == "open":
                return result | {"url": "https://example.invalid/#token=fixture"}
            if command == "restart":
                return result | {"error": "mutation-pending"}
            return result | {"pid": -1}

        monkeypatch.setattr(PrivateControl, "request", response)
        assert not selected.status().ready
        with pytest.raises(HeinzelError, match="valid local session"):
            selected.open_local()
        with pytest.raises(HeinzelError, match="installation change requires completion"):
            selected.restart()
    finally:
        control.close()


def test_web_removal_cannot_finish_until_the_owned_job_is_stopped(service):
    selected, manager, port = service
    selected.install(port)
    before = {path: path.read_bytes() for path in (selected.unit, selected.launcher, selected.marker)}
    with selected.removal_guard() as guarded, pytest.raises(HeinzelError, match="job is not stopped"):
        guarded.finish()
    assert manager.active
    assert {path: path.read_bytes() for path in before} == before
