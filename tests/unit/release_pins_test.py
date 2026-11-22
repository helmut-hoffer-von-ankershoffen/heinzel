"""release/pins.toml: every digest that release tooling reads at the tag equals the file it pins."""

import hashlib
import re
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
PINS = REPO / "release" / "pins.toml"
REQUIRED = {".github/workflows/acceptance.yml"}


def pins() -> dict[str, str]:
    data = tomllib.loads(PINS.read_text(encoding="utf-8"))
    assert set(data) == {"sha256"}, "release/pins.toml holds only the [sha256] table"
    return data["sha256"]


def test_release_pins_name_the_acceptance_workflow() -> None:
    assert set(pins()) >= REQUIRED


@pytest.mark.parametrize("path", sorted(REQUIRED))
def test_release_pin_matches_its_file(path: str) -> None:
    digest = pins()[path]
    assert re.fullmatch(r"[a-f0-9]{64}", digest)
    actual = hashlib.sha256((REPO / path).read_bytes()).hexdigest()
    assert digest == actual, f"{path} changed: review it, then set its sha256 in release/pins.toml to {actual}"


def test_every_release_pin_matches_its_file() -> None:
    for path, digest in pins().items():
        target = (REPO / path).resolve()
        assert target.is_relative_to(REPO), path
        assert target.is_file(), path
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest, path
