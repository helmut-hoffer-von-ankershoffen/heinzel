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
# Dark unless the preview `native_audio` is on (ADR 0166). Nothing is at these addresses: tests serve their own tone.
module.soundscape(
    Soundscape.model_validate(
        {
            "name": "fixture-album",
            "title": "Fixture album",
            "kind": "stream",
            "tracks": [
                {
                    "title": title,
                    "url": f"https://media.heinzel.team/fixture/{file}.mp3",
                    "type": "audio/mpeg",
                    "duration_seconds": 2,
                }
                for title, file in (("First tone", "one"), ("Second tone", "two"), ("Third tone", "three"))
            ],
        }
    )
)
module.soundscape(Path(__file__).parent / "soundscapes")
