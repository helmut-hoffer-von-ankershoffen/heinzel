"""Sharing status reads no peer roster and never changes OS networking or user configuration."""

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest
from heinzel_tailscale import sharing
from heinzel_tailscale._cli import TailscaleCli
from heinzel_tailscale._mapping import Mapping
from heinzel_tailscale._models import SharingIntent, SharingState, TailscaleSettings
from heinzel_tailscale._readiness import SharingReadiness
from heinzel_tailscale._service import Sharing

from heinzel import HeinzelError, LocalWebRuntime, Settings

pytestmark = pytest.mark.unit
DNS = "workstation.tail-example.ts.net."
MAPPING = Mapping.for_node(DNS, 8737)


@dataclass
class FakeCli(TailscaleCli):
    found: bool = True
    node: object = field(
        default_factory=lambda: {"BackendState": "Running", "Self": {"DNSName": DNS}, "CertDomains": [DNS[:-1]]}
    )
    serve: object = None
    error: bool = False
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def executable(self):
        return "/fake/tailscale" if self.found else None

    def json(self, arguments: Sequence[str]):
        self.calls.append(tuple(arguments))
        if self.error:
            msg = "The client did not answer."
            raise HeinzelError(msg)
        assert tuple(arguments) in {("status", "--json", "--peers=false"), ("serve", "status", "--json")}
        return self.node if arguments[0] == "status" else self.serve


class MemoryConfig:
    def __init__(self, settings, intent=None):
        self.root = settings
        self.choices = TailscaleSettings(intent=intent)

    def reread(self):
        return self

    def settings(self, model, _section):
        return self.root if model is Settings else self.choices


class NoWebUser:
    def context(self):
        msg = "No authenticated web request"
        raise HeinzelError(msg)


def service(settings, cli, *, owned=False):
    intent = (
        SharingIntent(
            enabled=True, origin=MAPPING.origin, backend_port=8737, https_port=443, added_origin=False, phase="owned"
        )
        if owned
        else None
    )
    return Sharing(settings, cli, MemoryConfig(settings, intent), SharingReadiness(LocalWebRuntime(), NoWebUser()))


def test_missing_client_reports_action_without_starting_a_command():
    cli = FakeCli(found=False)
    result = service(Settings(), cli).status()
    assert result.state == "unavailable"
    assert not result.installed
    assert not cli.calls


def test_logged_out_state_never_attempts_to_enable_serve():
    cli = FakeCli(node={"BackendState": "NeedsLogin"})
    result = service(Settings(), cli).status()
    assert result.state == "login-required"
    assert result.installed
    assert not result.logged_in
    assert cli.calls == [("status", "--json", "--peers=false")]


def test_read_failures_are_errors_not_claims_of_success():
    result = service(Settings(), FakeCli(error=True)).status()
    assert result.state == "error"
    assert result.origin is None


def test_no_mapping_is_off_and_contains_the_exact_nonsecret_url():
    result = service(Settings(), FakeCli()).status()
    assert result.state == "off"
    assert result.logged_in
    assert not result.mapped
    assert result.origin == "https://workstation.tail-example.ts.net"
    assert result.target == "http://127.0.0.1:8737"
    assert "token" not in result.model_dump_json()


def test_https_consent_is_required_before_an_existing_mapping_can_be_ready():
    cli = FakeCli(node={"BackendState": "Running", "Self": {"DNSName": DNS}}, serve=MAPPING.enable({}))
    result = service(Settings(), cli).status()
    assert result.state == "blocked"
    assert "HTTPS certificates" in result.message


def test_existing_mapping_requires_loaded_origin_and_serving_instance_verification():
    cli = FakeCli(serve=MAPPING.enable({}))
    result = service(Settings(), cli, owned=True).status()
    assert result.state == "apply-required"
    assert result.mapped
    loaded = service(Settings(ui_allowed_origins={MAPPING.origin}), cli, owned=True).status()
    assert loaded.state == "apply-required"
    assert "verify" in loaded.message


def test_preexisting_matching_manual_mapping_is_reported_as_unowned():
    result = service(Settings(ui_allowed_origins={MAPPING.origin}), FakeCli(serve=MAPPING.enable({}))).status()
    assert result.state == "blocked"
    assert result.mapped
    assert "not verified as owned" in result.message


@pytest.mark.parametrize(
    "serve",
    [
        {"Web": {MAPPING.authority: {"Handlers": {"/": {"Text": "Other app"}}}}},
        {"AllowFunnel": {MAPPING.authority: True}},
        [],
    ],
)
def test_conflicts_and_public_or_unsupported_config_are_blocked(serve):
    result = service(Settings(), FakeCli(serve=serve)).status()
    assert result.state == "blocked"
    assert result.logged_in
    assert not result.mapped


@pytest.mark.parametrize(
    "node", [{"BackendState": "Running"}, {"BackendState": "Running", "Self": {"DNSName": "evil.test"}}]
)
def test_missing_or_invalid_node_dns_is_not_a_callable_url(node):
    cli = FakeCli(node=node)
    result = service(Settings(), cli).status()
    assert result.state == "blocked"
    assert result.origin is None
    assert cli.calls == [("status", "--json", "--peers=false")]


def test_process_environment_keeps_absolute_path_entries_and_drops_credential_and_debug_settings(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:.:relative:/opt/bin")
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE", "do-not-forward")
    monkeypatch.setenv("TS_DEBUG_FAKE_ENV", "do-not-forward")
    monkeypatch.setenv("HTTPS_PROXY", "http://example.invalid")
    env = TailscaleCli.environment()
    assert env["PATH"] == "/usr/bin:/opt/bin"
    assert not {"HZ_CREDENTIAL_EXAMPLE", "TS_DEBUG_FAKE_ENV", "HTTPS_PROXY"} & env.keys()


def test_json_reads_refuse_unapproved_commands_before_starting_the_client():
    with pytest.raises(HeinzelError, match="approved command"):
        TailscaleCli().json(("funnel", "8737"))


def test_pending_control_exposes_the_request_without_claiming_on_or_exposing_a_url():
    item = sharing(
        SharingState(
            state="apply-required",
            installed=True,
            logged_in=True,
            desired_enabled=True,
            mapped=True,
            origin=MAPPING.origin,
            message="Restart required",
        )
    )
    assert item.toggle.requested is True
    assert item.toggle.checked is False
    assert item.toggle.url is None
    assert item.toggle.disable.operation == "disable"
