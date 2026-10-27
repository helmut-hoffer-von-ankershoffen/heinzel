"""The private pipe adapter bounds untrusted input and never echoes a rejected request."""

import io
import json
from types import SimpleNamespace

import pytest

from heinzel import _lifecycle_control as control

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "encoded",
    [
        b"not JSON: secret-fixture",
        b"x" * (control.MAX_REQUEST + 1),
        b'{"directory":"/owner/profile/lifecycle","action":"shell","secret":"fixture"}',
    ],
)
def test_invalid_or_oversized_private_request_is_redacted_before_execution(monkeypatch, capsys, encoded):
    monkeypatch.setattr(control.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(encoded)))
    monkeypatch.setattr(control, "execute", lambda _: pytest.fail("invalid control input reached transition authority"))
    control.main()
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": "The owned lifecycle action was refused; inspect this profile.",
    }


def test_valid_control_request_presents_only_the_typed_result(monkeypatch, capsys):
    encoded = b'{"directory":"/owner/profile/lifecycle","action":"recover"}'
    monkeypatch.setattr(control.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(encoded)))
    seen = []

    def execute(request):
        seen.append(request.action)
        return "settled"

    monkeypatch.setattr(control, "execute", execute)
    control.main()
    assert seen == ["recover"]
    assert json.loads(capsys.readouterr().out) == {"ok": True, "value": "settled"}
