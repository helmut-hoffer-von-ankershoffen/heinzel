"""Only the owning session may prove the exact nonsecret origin policy loaded at server startup."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from heinzel_tailscale import TailscaleCli
from heinzel_tailscale import module as tailscale_module
from heinzel_tailscale._mapping import Mapping
from pydantic import Field

from heinzel import Config, Module, ModuleSettings, Settings
from heinzel.heads import web
from heinzel.heads.web._devices import Devices

pytestmark = pytest.mark.integration
TOKEN = "owning-session-for-origin-policy-proof"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ONE = "https://workstation.tail-example.ts.net"
TWO = "https://other.example.test:9443"


def client_for(kernel, **options):
    return TestClient(
        web.build(kernel, port=8000, token=TOKEN, static=None, **options), base_url="http://127.0.0.1:8000"
    )


def expected(client, origins):
    running = client.get("/api/lifecycle", headers=AUTH).json()
    return {
        "instance": running["instance"],
        "version": running["version"],
        "settings": [{"section": "hz", "key": "ui_allowed_origins", "value": origins}],
    }


@pytest.mark.parametrize("origins", [[ONE, TWO], [TWO, ONE], ["https://WORKSTATION.tail-example.ts.net:443/", TWO]])
def test_owner_proves_canonical_origin_set_independently_of_order(make_kernel, origins):
    client = client_for(make_kernel(builtins=True, settings_=Settings(ui_allowed_origins={ONE, TWO})))
    body = expected(client, origins)
    response = client.post("/api/lifecycle/verify", headers=AUTH, json=body)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["matches"] is True
    assert response.json()["mismatched"] == []
    assert ONE not in response.text


def test_empty_startup_policy_can_be_proved_without_admitting_an_origin(make_kernel):
    client = client_for(make_kernel(builtins=True))
    assert client.post("/api/lifecycle/verify", headers=AUTH, json=expected(client, [])).json()["matches"]
    refused = client.get("/api/lifecycle", headers={**AUTH, "Host": "other.example.test"})
    assert refused.status_code == 403


def test_origin_proof_uses_startup_snapshot_after_saved_policy_changes(make_kernel, tmp_path):
    directory = Path(os.environ["HZ_CONFIG_HOME"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "config.toml"
    path.write_text(f'[hz]\nui_allowed_origins = ["{ONE}"]\n')
    config = Config.load(tmp_path)
    kernel = make_kernel(builtins=True, config=config, settings_=config.settings(Settings, "hz"))
    client = client_for(kernel)
    before = expected(client, [ONE])
    path.write_text(f'[hz]\nui_allowed_origins = ["{TWO}"]\n')
    assert client.post("/api/lifecycle/verify", headers=AUTH, json=before).json()["matches"]
    assert not client.post("/api/lifecycle/verify", headers=AUTH, json=expected(client, [TWO])).json()["matches"]


def test_actual_tailscale_apply_metadata_is_verified_by_the_replacement_owner(make_kernel, tmp_path):
    class StaticClient(TailscaleCli):
        def executable(self):
            return "/fake/tailscale"

        def json(self, arguments):
            if tuple(arguments) == ("status", "--json", "--peers=false"):
                return {
                    "BackendState": "Running",
                    "Self": {"DNSName": "workstation.tail-example.ts.net."},
                    "CertDomains": [ONE[8:]],
                }
            assert tuple(arguments) == ("serve", "status", "--json")
            return Mapping.for_node("workstation.tail-example.ts.net", 8000).enable({})

    def instance(config):
        kernel = make_kernel(
            builtins=True, tailscale=tailscale_module, config=config, settings_=config.settings(Settings, "hz")
        )
        kernel.bind(TailscaleCli, StaticClient())
        return client_for(kernel)

    config = Config.load(tmp_path)
    before = instance(config)
    directory = Path(os.environ["HZ_CONFIG_HOME"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.toml").write_text(
        f'[hz]\nui_allowed_origins = ["{ONE}"]\n'
        "[tailscale.intent]\nenabled = true\n"
        f'origin = "{ONE}"\nbackend_port = 8000\nhttps_port = 443\nadded_origin = true\nphase = "owned"\n'
    )
    row = before.get("/api/system/ui/status", params={"item": "tailscale:sharing"}, headers=AUTH).json()[0]
    assert row["problem"] is None
    assert row["item"]["toggle"]["checked"] is False
    apply = row["item"]["toggle"]["apply_settings"]
    assert apply == {"hz.ui_allowed_origins": [ONE]}
    assert not before.post(
        "/api/lifecycle/verify", headers=AUTH, json=expected(before, apply["hz.ui_allowed_origins"])
    ).json()["matches"]
    replacement = instance(config.reread())
    proof = replacement.post(
        "/api/lifecycle/verify", headers=AUTH, json=expected(replacement, apply["hz.ui_allowed_origins"])
    )
    assert proof.json()["matches"] is True
    ready = replacement.get("/api/system/ui/status", params={"item": "tailscale:sharing"}, headers=AUTH).json()[0]
    assert ready["item"]["toggle"]["checked"] is True


def test_process_environment_cannot_replace_the_expected_policy(make_kernel, monkeypatch):
    client = client_for(make_kernel(builtins=True, settings_=Settings(ui_allowed_origins={ONE})))
    body = expected(client, [ONE])
    monkeypatch.setenv("HZ_UI_ALLOWED_ORIGINS", TWO)
    monkeypatch.setenv("HZ_LOG_LEVEL", "invalid-runtime-value")
    assert client.post("/api/lifecycle/verify", headers=AUTH, json=body).json()["matches"]
    body["settings"][0]["value"] = [TWO]
    assert not client.post("/api/lifecycle/verify", headers=AUTH, json=body).json()["matches"]


@pytest.mark.parametrize(
    "value",
    [
        ONE,
        None,
        True,
        [123],
        ["http://unsafe.example"],
        [ONE + "/private"],
        ["https://user:secret@example.test"],
        ["https://*.example.test"],
        ["https://[broken"],
        [f"https://host{index}.example.test" for index in range(17)],
    ],
)
def test_invalid_policy_expectations_fail_closed_without_reflecting_values(make_kernel, value):
    client = client_for(make_kernel(builtins=True, settings_=Settings(ui_allowed_origins={ONE})))
    response = client.post("/api/lifecycle/verify", headers=AUTH, json=expected(client, value))
    assert response.status_code == 200
    assert response.json()["matches"] is False
    assert response.json()["mismatched"] == ["hz.ui_allowed_origins"]
    assert "secret" not in response.text
    assert "unsafe.example" not in response.text


def test_policy_proof_rejects_absent_invalid_and_paired_device_credentials(make_kernel, tmp_path):
    client = client_for(
        make_kernel(builtins=True, settings_=Settings(ui_allowed_origins={ONE})), devices=Devices(tmp_path / "devices")
    )
    body = expected(client, [ONE])
    assert client.post("/api/lifecycle/verify", json=body).status_code == 401
    assert client.post("/api/lifecycle/verify", headers={"Authorization": "Bearer wrong"}, json=body).status_code == 401
    pair = client.post("/api/_web/device", json={"name": "Fixture phone"}, headers=AUTH)
    assert pair.status_code == 200
    device = {"Authorization": f"Bearer {pair.json()['token']}", "X-Hz-Authentication": "web-session"}
    denied = client.post("/api/lifecycle/verify", headers=device, json=body)
    assert denied.status_code == 403
    assert ONE not in denied.text
    assert client.get("/api/lifecycle", headers=device).status_code == 200
    body["settings"] = [{"section": "hz", "key": "playful", "value": True}]
    assert client.post("/api/lifecycle/verify", headers=device, json=body).json()["matches"]


def test_allowlist_does_not_make_another_modules_private_policy_verifiable(make_kernel):
    module = Module("sample", "A separate private policy.")

    @module.settings
    class PrivatePolicy(ModuleSettings):
        ui_allowed_origins: list[str] = Field(default_factory=lambda: [ONE], json_schema_extra={"x-private": True})

    client = client_for(make_kernel(builtins=True, sample=module))
    body = expected(client, [ONE])
    body["settings"][0]["section"] = "sample"
    response = client.post("/api/lifecycle/verify", headers=AUTH, json=body)
    assert response.json()["matches"] is False
    assert response.json()["mismatched"] == ["sample.ui_allowed_origins"]
    assert ONE not in response.text


def test_policy_expectation_still_requires_exact_instance_version_and_unique_key(make_kernel):
    client = client_for(make_kernel(builtins=True, settings_=Settings(ui_allowed_origins={ONE})))
    body = expected(client, [ONE])
    for key in ("instance", "version"):
        wrong = body | {key: "different"}
        assert not client.post("/api/lifecycle/verify", headers=AUTH, json=wrong).json()["matches"]
    body["settings"] *= 2
    response = client.post("/api/lifecycle/verify", headers=AUTH, json=body)
    assert response.json()["matches"] is False
    assert response.json()["mismatched"] == ["hz.ui_allowed_origins"]
