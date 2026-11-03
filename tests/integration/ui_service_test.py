"""Persistent web integration uses isolated files and fake service managers; it never alters the host's jobs."""

import json
import os
import plistlib
import socket
import subprocess
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
        with pytest.raises(OSError, match="Address already in use"):
            service.install(foreign.getsockname()[1])
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

    monkeypatch.setattr(_service.shutil, "which", lambda _: None)
    opened = []
    monkeypatch.setattr(web, "serve", lambda kernel, **kwargs: opened.append(kwargs))
    result = CliRunner().invoke(build(make_kernel(builtins=True)), ["ui", "--no-browser"])
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
    custom_uv = service.home / "custom uv/uv"
    monkeypatch.setenv("HZ_STATE_DIR", str(service.directory.parent))
    monkeypatch.setattr(_service.shutil, "which", lambda name: str(custom_uv) if name == "uv" else None)
    monkeypatch.setattr(
        _service,
        "_run",
        lambda args: subprocess.CompletedProcess(args, 0, str(service.executable.parent), ""),
    )
    kernel = make_kernel(builtins=True)
    kernel.bind(Installation, SimpleNamespace(prefix=service.prefix, is_tool=True, startup_state_matches=lambda: True))
    created = WebService.for_user(kernel)
    assert created.prefix == service.prefix
    assert created.uv == custom_uv
    assert created.verify()
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


def test_preview_defaults_off_and_does_not_install_but_keeps_inspection_and_removal(make_kernel, service):
    service, manager, port = service
    kernel = make_kernel(builtins=True)
    kernel.bind(WebService, service)
    app = build(kernel)
    blocked = CliRunner().invoke(app, ["ui", "install", "--port", str(port), "--json"])
    assert blocked.exit_code == 1
    assert "disabled preview" in blocked.output
    assert not service.marker.exists()
    assert manager.calls == []
    service.install(port)  # An earlier enabled preview may still need safe cleanup after its flag is switched off.
    assert CliRunner().invoke(app, ["ui", "status", "--json"]).exit_code == 0
    assert CliRunner().invoke(app, ["ui", "restart", "--json"]).exit_code == 1
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
    result = CliRunner().invoke(build(kernel), ["ui", "--no-browser"])
    assert result.exit_code == 1
    assert "not ready" in result.output
    assert service.marker.is_symlink()


def test_disabled_preview_refuses_existing_attachment_instead_of_starting_a_competitor(
    make_kernel, service, monkeypatch
):
    from heinzel.heads import web

    service, _, port = service
    service.install(port)
    monkeypatch.setenv("HZ_STATE_DIR", str(service.directory.parent))
    monkeypatch.setattr(web, "serve", lambda *_args, **_kwargs: pytest.fail("Unsafe foreground fallback"))
    kernel = make_kernel(builtins=True)
    kernel.bind(WebService, service)
    result = CliRunner().invoke(build(kernel), ["ui", "--no-browser"])
    assert result.exit_code == 1
    assert "preview is disabled" in result.output


def test_supervised_startup_refuses_disabled_preview_before_serving(make_kernel, monkeypatch):
    from heinzel.heads import web

    monkeypatch.setattr(web, "serve", lambda *_args, **_kwargs: pytest.fail("Disabled preview started a server"))
    result = CliRunner().invoke(
        build(make_kernel(builtins=True)),
        ["ui", "--no-browser", "--port", "8737", "--supervised", "a" * 32],
    )
    assert result.exit_code == 1
    assert "preview is disabled" in result.output


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
