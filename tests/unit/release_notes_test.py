import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release_notes.py"
spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
assert spec
assert spec.loader
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)

CHANGELOG = """# Changelog

Intro.

## [0.2.0](https://example.com/v0.2.0) - 2026-11-01

### ✨ Features

- New thing

## [0.1.0](https://example.com/v0.1.0) - 2026-10-23

### 🐛 Bug fixes

- Old fix
"""


def test_extracts_the_section_of_a_version():
    assert release_notes.section(CHANGELOG, "v0.2.0") == "### ✨ Features\n\n- New thing\n"
    assert release_notes.section(CHANGELOG, "0.1.0") == "### 🐛 Bug fixes\n\n- Old fix\n"


def test_unknown_versions_fail():
    with pytest.raises(ValueError, match=r"no section for v9\.9\.9"):
        release_notes.section(CHANGELOG, "v9.9.9")
