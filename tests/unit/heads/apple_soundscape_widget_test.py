"""Guard the WidgetKit destination and transfer presentation contracts on both platforms."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
SOURCE = (Path(__file__).resolve().parents[3] / "apple/Widgets/HeinzelWidgets.swift").read_text(encoding="utf-8")


def view(name: str) -> str:
    start = SOURCE.index(f"var {name}:")
    return SOURCE[start : SOURCE.index("\n    }", start)]


@pytest.mark.parametrize("surface", ["artworkControl", "progressControl"])
def test_artwork_and_read_only_position_open_the_selected_track_or_picker(surface: str) -> None:
    body = view(surface)
    assert "if let id = state?.selectedID" in body
    assert "Link(destination: SoundscapeLink.track(id))" in body
    assert "Link(destination: SoundscapeLink.picker)" in body
    assert "Button(intent:" not in body
    assert "Slider(" not in view("timeline")
    assert "WatchSoundscapeIntent(trackID: id)" in view("information")


def test_album_cover_opens_picker_while_progress_still_targets_the_selected_track() -> None:
    assert "state?.albumArtwork != true" in view("artworkControl")
    assert "albumArtwork" not in view("progressControl")


def test_play_and_repeat_accept_a_loaded_selection_or_saved_playback() -> None:
    availability = view("canControlPlayback")
    assert "guard let state else { return false }" in availability
    assert 'state.playing || state.canPlaySaved || (state.selectedID != nil && state.origin != "none")' in availability
    assert ".disabled(!canControlPlayback)" in view("transport")
    assert ".disabled(!canControlPlayback)" in view("repeatControl")


def test_download_edge_is_attached_to_the_playback_rail_only_during_an_active_download() -> None:
    fraction = view("downloadFraction")
    assert 'state.phase == "downloading"' in fraction
    assert "state.verified < state.total" in fraction
    assert "state.downloadFraction" in fraction
    rail = view("timeline")
    assert rail.index("playbackProgress") < rail.index("if let downloadFraction") < rail.index(".fill(.yellow)")
    assert "geometry.size.width * downloadFraction" in rail
    assert "downloadLine" not in view("player")
