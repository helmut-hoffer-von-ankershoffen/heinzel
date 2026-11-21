"""`hz ui attach`: the desktop apps' one way in. It reaches the owned service over its real control socket, installs
the service when absent, and never starts a server of its own; isolated files and a fake service manager only."""

import json

import pytest
from typer.testing import CliRunner

from heinzel import HeinzelError
from heinzel.heads import web
from heinzel.heads.cli import build
from heinzel.heads.web._browser_bridge import BridgeSlot
from heinzel.heads.web._supervision import SupervisedWeb
from heinzel.kernel import PersistentWeb, private_read
from tests.integration.ui_service_test import INSTANCE, TOKEN, service, service_factory, start_control

pytestmark = pytest.mark.integration
__all__ = ["service", "service_factory"]  # the fixtures, for pytest
BRIDGE = "b" * 40


def test_two_attaches_return_the_same_instance_and_start_no_server(service):
    service, manager, port = service
    service.install(port)
    control = start_control(service, port)
    try:
        first, second = service.attach(port), service.attach(port)
        assert first == second == f"http://127.0.0.1:{port}/#token={TOKEN}"
        assert service.status().instance == INSTANCE
        loads = [call for call in manager.calls if call[1:2] in (["bootstrap"], ["enable"], ["--user"])]
        assert len([call for call in loads if "bootstrap" in call or "enable" in call]) == 1  # the install only
    finally:
        control.close()


def test_first_launch_installs_the_service_and_waits_until_it_is_ready(service):
    service, _manager, port = service
    controls = []
    service._sleep = lambda _seconds: controls or controls.append(start_control(service, port))
    try:
        assert service.attach(port) == f"http://127.0.0.1:{port}/#token={TOKEN}"
        assert service.status().installed
    finally:
        for control in controls:
            control.close()


def test_attach_registers_the_desktop_bridge_token_with_the_live_server(service):
    service, _manager, port = service
    service.install(port)
    slot = BridgeSlot(TOKEN, None)
    record = json.loads(private_read(service.marker))
    control = SupervisedWeb(
        service.directory,
        generation=record["generation"],
        port=port,
        instance=INSTANCE,
        version="1.2.3",
        url=f"http://127.0.0.1:{port}/#token={TOKEN}",
        restart=lambda: None,
        register_bridge=slot.register,
    )
    try:
        assert slot.current() is None  # a service no desktop app started refuses every bridge socket
        service.attach(port, bridge=BRIDGE)
        assert slot.current() == BRIDGE
        with pytest.raises(HeinzelError, match="browser bridge"):
            service.attach(port, bridge=TOKEN)  # the session token itself is never a bridge token
        assert slot.current() == BRIDGE
    finally:
        control.close()


def test_cli_attach_prints_one_link_retires_strays_and_never_serves(make_kernel, monkeypatch):
    kernel = make_kernel(builtins=True)
    calls = []
    kernel.bind(
        PersistentWeb,
        PersistentWeb(
            open_local=lambda: pytest.fail("attach uses attach, not open"),
            attach=lambda port, bridge: calls.append(("attach", port, bridge)) or "http://127.0.0.1:8737/#token=x",
            retire_strays=lambda: calls.append(("retire",)) or ([25616], []),
        ),
    )
    monkeypatch.setattr(web, "serve", lambda *_args, **_kwargs: pytest.fail("attach started a server"))
    result = CliRunner().invoke(build(kernel), ["ui", "attach", "--bridge-token-stdin", "--json"], input=BRIDGE + "\n")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"url": "http://127.0.0.1:8737/#token=x"}
    assert calls == [("retire",), ("attach", kernel.settings.ui_port, BRIDGE)]
    assert "Stopped 1 leftover web server" in result.stderr


def test_cli_attach_fails_with_one_reason_and_no_fallback_server(make_kernel, monkeypatch):
    kernel = make_kernel(builtins=True)

    def refuse(_port, _bridge):
        msg = "The web service did not become ready. Inspect `hz ui status`."
        raise HeinzelError(msg)

    kernel.bind(PersistentWeb, PersistentWeb(open_local=lambda: None, attach=refuse))
    monkeypatch.setattr(web, "serve", lambda *_args, **_kwargs: pytest.fail("attach fell back to a server"))
    result = CliRunner().invoke(build(kernel), ["ui", "attach"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "did not become ready" in result.stderr
