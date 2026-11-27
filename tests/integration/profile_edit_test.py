"""Profile settings survive a new web app and kernel reading the isolated user's files."""

import pytest
from fastapi.testclient import TestClient

from heinzel import Config
from heinzel.heads import web

pytestmark = pytest.mark.integration

TOKEN = "profile-edit-test-token-long-enough"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize("initial", [None, "mira"])
def test_edit_profile_and_read_from_new_backend(make_kernel, tmp_path, initial):
    def backend():
        kernel = make_kernel(builtins=True, config=Config.load(tmp_path))
        return TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000")

    with backend() as client:
        if initial is not None:
            seeded = client.post(
                "/api/system/config/set",
                headers=AUTH,
                params={"key": "org.handle", "value": initial, "scope": "user"},
            )
            assert seeded.status_code == 200, seeded.text
    with backend() as client:
        assert client.get("/api/org/show", headers=AUTH).json()["handle"] == initial
        saved = client.post(
            "/api/system/config/set",
            headers=AUTH,
            params={"key": "org.handle", "value": "mira-two", "scope": "user"},
        )
        assert saved.status_code == 200, saved.text
    with backend() as fresh:
        shown = fresh.get("/api/org/show", headers=AUTH)
        assert shown.status_code == 200, shown.text
        assert shown.json()["handle"] == "mira-two"
