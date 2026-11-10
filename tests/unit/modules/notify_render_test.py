"""The CLI tells people why a recorded notification has not appeared."""

from datetime import UTC, datetime
from io import StringIO

import pytest
from rich.console import Console

from heinzel import Notice, NoticeKind
from heinzel.kernel._notifications import Delivery
from heinzel.modules.system.notify._models import NoticeList
from heinzel.modules.system.notify._render import render_list

pytestmark = pytest.mark.unit


def text(result: NoticeList) -> str:
    output = StringIO()
    Console(file=output, width=160, color_system=None).print(render_list(result))
    return output.getvalue()


def test_empty_list_explains_how_to_test_notifications():
    shown = text(NoticeList(notices=[], waiting=0, native="unavailable"))
    assert "No notices yet" in shown
    assert "hz system notify test" in shown
    assert "Outside the web app: unavailable" in shown


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"delivered_by": Delivery.WEB}, "shown (web)"),
        ({"popup": False, "held": "quiet mode"}, "in the list only: quiet mode"),
        ({"due": datetime(2026, 11, 10, 9, tzinfo=UTC)}, "start of your working hours"),
        ({}, "waits to pop up"),
    ],
)
def test_notice_list_explains_delivery_state(updates, expected):
    at = datetime(2026, 11, 10, 8, tzinfo=UTC)
    values = {
        "id": "n1",
        "kind": NoticeKind.DOCTOR,
        "title": "Health",
        "body": "Retry",
        "route": "/doctor",
        "command": "hz system doctor",
        "key": "health",
        "at": at,
        "due": at,
        "popup": True,
    }
    values.update(updates)
    shown = text(NoticeList(notices=[Notice(**values)], waiting=1, native="available"))
    assert "Health" in shown
    assert expected in shown
    assert "1 waiting" in shown
