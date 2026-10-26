"""A successful response from the retiring UI process cannot acknowledge a completed restart."""

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, SecretStr

from heinzel import Config, Module, ModuleSettings, __version__
from heinzel.heads import web

pytestmark = pytest.mark.integration
TOKEN = "t" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_lifecycle_is_authenticated_stable_and_replaced_with_the_server(make_kernel):
    kernel = make_kernel(builtins=True)
    calls = []
    client = TestClient(
        web.build(kernel, port=8000, token=TOKEN, static=None, restart=lambda: calls.append(1)),
        base_url="http://127.0.0.1:8000",
    )
    assert client.get("/api/lifecycle").status_code == 401
    before = client.get("/api/lifecycle", headers=AUTH)
    assert before.headers["Cache-Control"] == "no-store"
    assert before.json()["restartable"] is True
    assert before.json()["version"] == __version__
    answer = client.post("/api/restart", headers=AUTH)
    assert answer.status_code == 202
    assert calls == [1]
    assert answer.json()["instance"] == before.json()["instance"]
    assert client.get("/api/lifecycle", headers=AUTH).json()["instance"] == answer.json()["instance"]
    successor = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000")
    after = successor.get("/api/lifecycle", headers=AUTH).json()
    assert after["instance"] != answer.json()["instance"]
    assert after["restartable"] is False


@pytest.fixture
def sample_module():
    module = Module("sample", "Example settings.")

    class Nested(BaseModel):
        name: str = "visible"
        value: SecretStr = SecretStr("protected")

    @module.settings
    class ExampleSettings(ModuleSettings):
        count: int = Field(default=1, ge=0, le=10)
        options: dict[str, int] = Field(default_factory=lambda: {"one": 1, "two": 2})
        private: str = Field(default="protected", json_schema_extra={"x-private": True})
        concealed: SecretStr = SecretStr("protected")
        api_token: str = "protected"
        hidden: str = Field(default="protected", json_schema_extra={"writeOnly": True})
        nested: Nested = Field(default_factory=Nested)

    return module


def lifecycle_client(kernel, **kwargs):
    return TestClient(
        web.build(kernel, port=8000, token=TOKEN, static=None, **kwargs), base_url="http://127.0.0.1:8000"
    )


def expectation(client, settings):
    status = client.get("/api/lifecycle", headers=AUTH).json()
    return {"instance": status["instance"], "version": status["version"], "settings": settings}


def test_loaded_configuration_is_bound_to_instance_and_strict_json_types(make_kernel, sample_module):
    client = lifecycle_client(make_kernel(sample=sample_module))
    body = expectation(client, [{"section": "sample", "key": "count", "value": 1}])
    result = client.post("/api/lifecycle/verify", headers=AUTH, json=body)
    assert result.status_code == 200
    assert result.headers["Cache-Control"] == "no-store"
    assert result.json() == {"instance": body["instance"], "version": __version__, "matches": True, "mismatched": []}
    for key, value in (("instance", "different"), ("version", "0.0.0")):
        assert not client.post("/api/lifecycle/verify", headers=AUTH, json={**body, key: value}).json()["matches"]
    for value in (True, "1", 1.0, None, 11, -1):
        body["settings"][0]["value"] = value
        assert not client.post("/api/lifecycle/verify", headers=AUTH, json=body).json()["matches"]


def test_verification_reads_startup_snapshot_instead_of_changed_configuration(make_kernel, sample_module, tmp_path):
    path = tmp_path / "hz.toml"
    path.write_text("[sample]\ncount = 2\n")
    kernel = make_kernel(builtins=True, sample=sample_module, config=Config.load(tmp_path))
    client = lifecycle_client(kernel)
    body = expectation(client, [{"section": "sample", "key": "count", "value": 2}])
    path.write_text("[sample]\ncount = 3\n")
    report = client.get("/api/system/config/show", headers=AUTH).json()
    assert (
        next(item["value"] for item in report["settings"] if item["section"] == "sample" and item["key"] == "count")
        == 3
    )
    assert client.post("/api/lifecycle/verify", headers=AUTH, json=body).json()["matches"]
    body["settings"][0]["value"] = 3
    assert not client.post("/api/lifecycle/verify", headers=AUTH, json=body).json()["matches"]
    successor = lifecycle_client(make_kernel(sample=sample_module, config=Config.load(tmp_path)))
    body = expectation(successor, body["settings"])
    assert successor.post("/api/lifecycle/verify", headers=AUTH, json=body).json()["matches"]


@pytest.mark.parametrize("key", ["private", "concealed", "api_token", "hidden", "nested", "unknown"])
def test_private_secret_and_unknown_fields_never_become_equality_oracles(make_kernel, sample_module, key):
    client = lifecycle_client(make_kernel(sample=sample_module))
    body = expectation(client, [{"section": "sample", "key": key, "value": "protected"}])
    for guess in ("protected", "**********", "wrong", None):
        body["settings"][0]["value"] = guess
        result = client.post("/api/lifecycle/verify", headers=AUTH, json=body)
        assert result.json() == {
            "instance": body["instance"],
            "version": __version__,
            "matches": False,
            "mismatched": [f"sample.{key}"],
        }
        assert "protected" not in result.text


def test_verification_accepts_reordered_objects_but_refuses_duplicate_keys(make_kernel, sample_module):
    client = lifecycle_client(make_kernel(sample=sample_module))
    setting = {"section": "sample", "key": "options", "value": {"two": 2, "one": 1}}
    body = expectation(client, [setting])
    assert client.post("/api/lifecycle/verify", headers=AUTH, json=body).json()["matches"]
    body["settings"].append(setting)
    assert not client.post("/api/lifecycle/verify", headers=AUTH, json=body).json()["matches"]


def test_verification_authenticates_and_bounds_streamed_json_before_parsing(make_kernel):
    client = lifecycle_client(make_kernel())
    path = "/api/lifecycle/verify"
    assert client.post(path, json={}).status_code == 401
    assert client.post(path, headers={**AUTH, "Origin": "https://example.com"}, json={}).status_code == 403
    assert client.post(path, headers={**AUTH, "Sec-Fetch-Site": "cross-site"}, json={}).status_code == 403
    assert client.post(path, headers={**AUTH, "Host": "example.com"}, json={}).status_code == 403
    assert client.post(path, headers=AUTH, content=b"x" * (64 * 1024 + 1)).status_code == 413
    assert client.post(path, headers=AUTH, content=iter([b"x" * 32_000] * 3)).status_code == 413
    assert client.post(path, headers=AUTH, content=b'{"private":"protected"').status_code == 422
    assert "protected" not in client.post(path, headers=AUTH, json={"private": "protected"}).text
    body = expectation(client, [{"section": "hz", "key": "playful", "value": True}] * 101)
    assert client.post(path, headers=AUTH, json=body).status_code == 422


@pytest.mark.parametrize("value", ["NaN", "Infinity", '{"nested":NaN}'])
def test_nonfinite_json_expectations_are_rejected_without_an_unhandled_error(make_kernel, value):
    client = lifecycle_client(make_kernel())
    body = '{"instance":"x","version":"x","settings":[{"section":"hz","key":"playful","value":' + value + "}]}"
    result = client.post("/api/lifecycle/verify", headers=AUTH, content=body)
    assert result.status_code == 422
    assert "finite JSON" in result.text


def test_restart_refusal_never_schedules_a_restart(make_kernel):
    client = lifecycle_client(make_kernel())
    result = client.post("/api/restart", headers=AUTH)
    assert result.status_code == 400
    assert "can't restart" in result.json()["error"]
