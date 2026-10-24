import pytest
from heinzel_zoom import ZoomService, ZoomSettings

from heinzel import Status

pytestmark = pytest.mark.unit


class Bound:
    def __init__(self, **bound: str) -> None:
        self.bound_to = bound

    def bound(self, capability: str) -> str | None:
        return self.bound_to.get(capability)


def checks(bindings: Bound, settings: ZoomSettings | None = None):
    service = ZoomService(kernel=None, settings=settings or ZoomSettings())  # type: ignore[arg-type]
    return {check.name: check for check in service.checks(bindings)}


def test_bound_to_mail_and_meetings_everything_is_up_and_says_where_summaries_are_switched_on():
    found = checks(Bound(mail="google", meetings="zoom"), ZoomSettings(query="from:zoom.us label:Meetings"))
    assert {name: check.status for name, check in found.items()} == {
        "summaries": Status.UP,
        "mail": Status.UP,
        "meetings": Status.UP,
    }
    assert "AI Companion > Meeting summary with AI Companion" in found["summaries"].detail
    assert "`from:zoom.us label:Meetings`" in found["summaries"].detail
    assert "no mail is read" in found["summaries"].detail
    assert found["mail"].detail == "summaries are read through google"
    assert found["meetings"].detail == "served by zoom"


def test_without_mail_the_check_is_degraded_with_the_binding_to_write():
    found = checks(Bound(meetings="zoom"))
    assert found["mail"].status is Status.DEGRADED
    assert found["mail"].fix == "hz system config set capabilities.mail <a mail adapter, e.g. google>"


@pytest.mark.parametrize(("bound", "detail"), [({}, "not bound"), ({"meetings": "rooms"}, "served by rooms, not zoom")])
def test_installed_but_not_bound_is_degraded_with_the_binding_to_write(bound, detail):
    found = checks(Bound(mail="google", **bound))
    assert found["meetings"].detail == detail
    assert (found["bindings"].status, found["bindings"].fix) == (
        Status.DEGRADED,
        "hz system config set capabilities.meetings zoom",
    )
