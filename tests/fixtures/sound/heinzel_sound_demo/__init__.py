"""A made-up plugin for tests that offers one tiny layered soundscape (ADR 0058); no code beyond the declaration."""

from pathlib import Path

from heinzel import Module

module = Module("sound-demo", "A tiny layered soundscape for tests.")
module.soundscape(Path(__file__).parent / "soundscapes")
