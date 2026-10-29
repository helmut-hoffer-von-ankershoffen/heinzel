"""A made-up plugin offering a synthetic stream and a tiny layered soundscape through public declarations."""

from pathlib import Path

from heinzel import Module, Soundscape

module = Module("sound-demo", "A tiny layered soundscape for tests.")
module.soundscape(
    Soundscape.model_validate(
        {
            "name": "fixture-radio",
            "title": "Fixture radio",
            "kind": "stream",
            "stream": {"provider": "youtube", "video": "abcdefghijk"},
        }
    )
)
module.soundscape(Path(__file__).parent / "soundscapes")
