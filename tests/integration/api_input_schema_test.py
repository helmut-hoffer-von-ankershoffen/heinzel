"""Private normal and streaming routes reuse their strict body model within one app build."""

import inspect
from typing import Annotated, get_args

import pytest
from fastapi.testclient import TestClient
from pydantic import Field

from heinzel import Module
from heinzel.heads import api

pytestmark = pytest.mark.integration


def example(annotation):
    module = Module("example", "Synthetic private operation.")

    def send(value):
        """Return the validated private value."""
        return {"value": value}

    send.__annotations__ = {"value": annotation, "return": dict}
    module.operation(private=True, read_only=False)(send)
    return module


def body_model(app, path):
    route = next(route for route in app.routes if route.path == path)
    return get_args(inspect.signature(route.endpoint).parameters["_input"].annotation)[0]


def test_private_normal_and_stream_routes_share_one_strict_model(make_kernel):
    kernel = make_kernel(example=example(Annotated[int, Field(ge=2)]))
    app = api.build(kernel, head="web", token="fixture-session")
    model = body_model(app, "/example/send")
    assert model is body_model(app, "/stream/example/send")
    assert model.model_config["extra"] == "forbid"
    client = TestClient(app, base_url="http://localhost")
    headers = {"Authorization": "Bearer fixture-session"}
    for path in ("/example/send", "/stream/example/send"):
        assert client.post(path, json={"value": 3}).status_code == 401
        assert client.post(path, headers=headers, json={"value": 1}).status_code == 422
        assert client.post(path, headers=headers, json={"value": 3, "extra": "refused"}).status_code == 422
        response = client.post(path, headers=headers, json={"value": 3})
        assert response.status_code == 200, response.text
    schema = client.get("/openapi.json", headers=headers).json()
    assert schema["components"]["schemas"]["example_send_Input"]["additionalProperties"] is False
    assert schema["components"]["schemas"]["example_send_Input"]["properties"]["value"]["minimum"] == 2


def test_separate_app_builds_keep_distinct_private_input_types(make_kernel):
    first = api.build(make_kernel(example=example(int)), head="web", token="one")
    second = api.build(make_kernel(example=example(str)), head="web", token="two")
    one = body_model(first, "/example/send")
    two = body_model(second, "/example/send")
    assert one is not two
    assert one.model_fields["value"].annotation is int
    assert two.model_fields["value"].annotation is str
