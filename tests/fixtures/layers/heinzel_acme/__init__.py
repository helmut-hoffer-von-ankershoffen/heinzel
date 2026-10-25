"""Acme, a made-up company: the root layer of the test tree (ADR 0040). It brings an adapter and a theme."""

from datetime import UTC, datetime
from pathlib import Path

from heinzel import Module, Soundscape, ThemeSignals

module = Module("acme", "Acme's adapters and defaults: a made-up company for tests.")
module.layer()


@module.provides("tracker", modes={"read"})
def board() -> object:
    """Acme's tracker; never built in these tests."""
    return object()


module.playbook(
    "How Acme works: its tracker and calendar.",
    defaults={"capabilities.tracker": "acme", "capabilities.calendar": "acme"},
)
module.theme(Path(__file__).parent / "themes")
# A stream soundscape of the company's, which the team's of the same name hides (ADR 0058).
module.soundscape(
    Soundscape.model_validate(
        {
            "name": "acme-radio",
            "title": "Acme radio",
            "kind": "stream",
            "stream": {"provider": "youtube", "video": "tRsQsTMvPNg"},
        }
    )
)


@module.signals(every="10m")
def weather() -> ThemeSignals:
    """Acme's made-up weather for themes: always rain, with the sun times of a spring day."""
    return ThemeSignals(
        weather="rain",
        sunrise=datetime(2026, 4, 1, 4, 50, tzinfo=UTC),
        sunset=datetime(2026, 4, 1, 17, 40, tzinfo=UTC),
    )
