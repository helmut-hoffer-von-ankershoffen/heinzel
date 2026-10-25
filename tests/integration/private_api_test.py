"""Private operations use one owner and fail closed across local and authenticated network heads."""

import json
import logging
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from typer.testing import CliRunner

from heinzel import Config, CurrentUser, HeinzelError, Module, Principal, Settings
from heinzel.heads import api, cli, mcp
from heinzel.heads._exposure import API_PRIVATE, MCP_HTTP, exposed
from heinzel.heads.api._auth import ApiBearer

pytestmark = pytest.mark.integration

NOW = datetime(2026, 1, 1, tzinfo=UTC)
BASE = "http://localhost"


def private_module(barrier=None):
    module = Module("personal", "Private test operations.")

    @module.operation(private=True)
    def who(user: CurrentUser, hold: bool = False) -> dict[str, str]:
        """Return only this call's trusted identity."""
        before = user.context()
        if hold:
            barrier.wait(timeout=3)
        after = user.context()
        assert before == after
        return {"owner": after.principal.id, "credential": after.credential_id, "auth": after.authentication}

    @module.operation(private=True, read_only=False)
    def change(user: CurrentUser) -> str:
        """A private mutation."""
        return user.principal().id

    @module.operation(private=True, read_only=False)
    def send(message: str, user: CurrentUser) -> dict[str, str]:
        """Receive sensitive input only through a private request body."""
        return {"message": message, "owner": user.principal().id}

    @module.operation
    def public(user: CurrentUser) -> str:
        """A public route must not inherit a local call identity."""
        return user.principal().id

    return module


def protected(kernel, *, permissions=None, audience="server-a", credential_id="one"):
    token = ApiBearer.issue(kernel.resolve(CurrentUser).owner(), audience, now=NOW)
    token = replace(token, credential_id=credential_id)
    if permissions is not None:
        token = replace(token, permissions=permissions)
    app = api.build(kernel, credentials=token, audience=audience, clock=lambda: NOW)
    return app, {"Authorization": f"Bearer {token.secret}"}, token


def test_private_operations_are_absent_publicly_and_enforced_in_the_kernel(make_kernel):
    kernel = make_kernel(personal=private_module())
    private = next(item.operation for item in exposed(kernel, API_PRIVATE) if item.name == "personal_who")
    with pytest.raises(HeinzelError, match="authenticated"):
        kernel.invoke(private)
    public = TestClient(api.build(kernel), base_url=BASE)
    assert public.get("/personal/who").status_code == 404
    assert "/personal/who" not in public.get("/openapi.json").json()["paths"]
    assert not any(item.operation.private for item in exposed(kernel, MCP_HTTP))
    user = kernel.resolve(CurrentUser)
    with user.local("cli"):
        assert public.get("/personal/public").status_code == 400
        assert user.principal() == user.owner()
    with pytest.raises(HeinzelError, match="authenticated"):
        user.context()


def test_private_api_requires_exact_credential_and_preserves_host_origin_guards(make_kernel):
    kernel = make_kernel(personal=private_module())
    app, auth, token = protected(kernel)
    client = TestClient(app, base_url=BASE)
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"X-Hz-Principal": "u-" + "b" * 32}):
        assert client.get("/personal/who", headers=headers).status_code == 401
    result = client.get("/personal/who", headers={**auth, "X-Hz-Principal": "u-" + "b" * 32})
    assert result.json()["owner"] == kernel.resolve(CurrentUser).owner().id
    assert result.headers["Cache-Control"] == "no-store"
    assert token.secret not in result.text
    assert client.get("/personal/who", headers={**auth, "Origin": "http://evil.example.test"}).status_code == 403
    assert client.get("/personal/who", headers={**auth, "Host": "evil.example.test"}).status_code == 403
    assert client.get("/docs", headers=auth).status_code == 404
    schema = client.get("/openapi.json", headers=auth).json()
    assert schema["components"]["securitySchemes"]["PrivateApiBearer"]["scheme"] == "bearer"
    assert schema["paths"]["/personal/who"]["get"]["security"] == [{"PrivateApiBearer": []}]
    expired = TestClient(
        api.build(kernel, credentials=token, audience=token.audience, clock=lambda: token.expires_at), base_url=BASE
    )
    assert expired.get("/personal/who", headers=auth).status_code == 401
    with pytest.raises(HeinzelError):
        api.build(kernel, credentials=token, audience="other-server")
    with pytest.raises(HeinzelError):
        api.build(kernel, head=API_PRIVATE, token="legacy-token")
    with pytest.raises(HeinzelError):
        api.build(kernel, head="web")
    with pytest.raises(HeinzelError):
        api.build(kernel, head="cli")


def test_two_concurrent_credentials_and_streams_never_share_scopes(make_kernel):
    kernel = make_kernel(personal=private_module(threading.Barrier(2)))
    read_app, read_auth, _ = protected(kernel, permissions=frozenset({"private:read"}), credential_id="reader")
    all_app, all_auth, _ = protected(kernel, audience="server-b", credential_id="writer")

    async def scenario():
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=read_app), base_url=BASE) as reader,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=all_app), base_url=BASE) as writer,
        ):
            results = {}

            async def call(client, auth, key):
                results[key] = (await client.get("/personal/who?hold=true", headers=auth)).json()

            async with anyio.create_task_group() as group:
                group.start_soon(call, reader, read_auth, "reader")
                group.start_soon(call, writer, all_auth, "writer")
            assert results["reader"]["credential"] == "reader"
            assert results["writer"]["credential"] == "writer"
            assert (await reader.post("/personal/change", headers=read_auth)).status_code == 400
            assert (await writer.post("/personal/change", headers=all_auth)).status_code == 200

            async def stream(client, auth, key):
                response = await client.post("/stream/personal/who", headers=auth, json={"hold": True})
                result = next(line for line in response.text.splitlines() if line.startswith("data:"))
                results[key] = json.loads(result[5:])["data"]

            async with anyio.create_task_group() as group:
                group.start_soon(stream, reader, read_auth, "reader")
                group.start_soon(stream, writer, all_auth, "writer")
            assert results["reader"]["credential"] == "reader"
            assert results["writer"]["credential"] == "writer"

    anyio.run(scenario)
    with pytest.raises(HeinzelError, match="authenticated"):
        kernel.resolve(CurrentUser).context()


def test_cli_stdio_mcp_and_private_api_share_one_installation_owner(make_kernel):
    kernel = make_kernel(personal=private_module())
    local = CliRunner().invoke(cli.build(kernel), ["personal", "who", "--json"])
    assert local.exit_code == 0, local.output
    owner = json.loads(local.output)["owner"]
    app, auth, _ = protected(kernel)
    assert TestClient(app, base_url=BASE).get("/personal/who", headers=auth).json()["owner"] == owner

    async def scenario():
        async with Client(mcp.build(kernel)) as client:
            result = await client.call_tool("personal_who", {})
            assert not result.is_error
            assert result.structured_content["owner"] == owner
            assert result.structured_content["auth"] == "mcp-stdio"

    anyio.run(scenario)


def test_project_state_override_cannot_select_owner_or_default_api_credentials(tmp_path, monkeypatch, make_kernel):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    attacker = project / "chosen-state"
    (attacker / "identity").mkdir(parents=True, mode=0o700)
    seeded = attacker / "identity" / "owner-v1"
    seeded.write_text("u-" + "e" * 32 + "\n")
    seeded.chmod(0o600)
    (project / "hz.toml").write_text(f'[hz]\nstate_dir = "{attacker}"\n')
    trusted = tmp_path / "trusted-state"
    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / "config.toml").write_text(f'[hz]\nstate_dir = "{trusted}"\n')
    monkeypatch.setenv("HZ_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("HZ_STATE_DIR")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(project)
    config = Config.load(project)
    kernel = make_kernel(config=config, settings_=config.settings(Settings, "hz"))
    user = kernel.resolve(CurrentUser)
    assert kernel.settings.state_dir == attacker
    assert user.directory == trusted / "identity"
    assert user.owner() != Principal("u-" + "e" * 32)
    monkeypatch.setattr(api, "boot", lambda: kernel)
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)
    api.serve(private=True)
    assert not (attacker / "api").exists()
    assert len(list(user.directory.parent.glob("api/*.json"))) == 1


def test_an_owner_is_stable_private_and_shared_across_processes(tmp_path):
    script = (
        "from pathlib import Path; from heinzel import CurrentUser; import sys; "
        "print(CurrentUser(Path(sys.argv[1])).owner().id)"
    )
    for iteration in range(3):
        directory = tmp_path / str(iteration) / "identity"
        processes = [
            subprocess.Popen([sys.executable, "-c", script, str(directory)], stdout=subprocess.PIPE, text=True)
            for _ in range(3)
        ]
        try:
            owners = [process.communicate(timeout=4)[0].strip() for process in processes]
            assert all(process.returncode == 0 for process in processes)
            assert owners == [CurrentUser(directory).owner().id] * 3
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=4)
        assert directory.stat().st_mode & 0o777 == 0o700
        assert (directory / "owner-v1").stat().st_mode & 0o777 == 0o600
        assert (directory / "owner.lock").stat().st_mode & 0o777 == 0o600


def test_private_api_cli_creates_a_credential_without_printing_the_secret(tmp_path, monkeypatch, make_kernel):
    kernel = make_kernel(builtins=True)
    monkeypatch.setattr(api, "boot", lambda: kernel)
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)
    path = tmp_path / "private" / "client.json"
    result = CliRunner().invoke(
        cli.build(kernel), ["system", "serve", "api", "--private", "--credential-file", str(path)]
    )
    assert result.exit_code == 0, result.output
    token = json.loads(path.read_text())["token"]
    assert "credential saved" in result.output
    assert token not in result.output
    result = CliRunner().invoke(cli.build(kernel), ["system", "serve", "api", "--credential-file", str(path)])
    assert result.exit_code == 2
    assert "--private" in result.output


def test_private_mutations_and_streams_use_strict_flat_json_on_web_and_api(make_kernel, caplog):
    caplog.set_level(logging.INFO)
    kernel = make_kernel(personal=private_module())
    private, auth, _ = protected(kernel)
    paired = api.build(kernel, head="web", token="paired-test-session")
    secret_text = "sensitive example content stays out of URLs"
    for app, headers in ((private, auth), (paired, {"Authorization": "Bearer paired-test-session"})):
        client = TestClient(app, base_url=BASE)
        response = client.post("/personal/send", headers=headers, json={"message": secret_text})
        assert response.status_code == 200
        assert response.json()["message"] == secret_text
        assert client.post("/personal/change", headers=headers, json={"unknown": "refused"}).status_code == 422
        assert secret_text not in str(response.request.url)
        assert (
            client.post(
                "/personal/send", headers=headers, json={"message": secret_text, "unknown": "refused"}
            ).status_code
            == 422
        )
        response = client.post("/stream/personal/send", headers=headers, json={"message": secret_text})
        assert response.status_code == 200
        assert secret_text in response.text
        assert (
            client.post(
                "/stream/personal/send", headers=headers, json={"message": secret_text, "unknown": "refused"}
            ).status_code
            == 422
        )
        assert secret_text not in str(response.request.url)
        assert client.post("/personal/send", headers=headers, params={"message": "wrong-transport"}).status_code == 422
        operation = client.get("/openapi.json", headers=headers).json()["paths"]["/personal/send"]["post"]
        assert "requestBody" in operation
        assert not operation.get("parameters")
        schema = client.get("/openapi.json", headers=headers).json()
        assert schema["components"]["schemas"]["personal_send_Input"]["additionalProperties"] is False
    assert secret_text not in caplog.text


def test_concurrent_first_stdio_mcp_calls_share_the_trusted_owner(make_kernel):
    kernel = make_kernel(personal=private_module(threading.Barrier(2)))

    async def scenario():
        async with Client(mcp.build(kernel)) as client:
            results = []

            async def call():
                result = await client.call_tool("personal_who", {"hold": True})
                assert not result.is_error
                results.append(result.structured_content)

            async with anyio.create_task_group() as group:
                group.start_soon(call)
                group.start_soon(call)
            assert len(results) == 2
            assert results[0] == results[1]
            assert results[0]["auth"] == "mcp-stdio"
            assert results[0]["owner"] == kernel.resolve(CurrentUser).owner().id

    anyio.run(scenario)
