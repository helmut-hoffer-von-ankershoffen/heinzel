"""Published changelog sections survive later release generation."""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/changelog_intros.py"
spec = importlib.util.spec_from_file_location("changelog_intros", SCRIPT)
assert spec
assert spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

PREVIOUS = (
    "# Changelog\n\n## [0.1.0] - old\n\nA hand-written intro.\n\n### Features\n\n"
    "- Historical entry from an unreachable commit.\n"
)
GENERATED = "# Changelog\n\n## [0.1.1] - new\n\n### Fixes\n\n- New fix.\n\n## [0.1.0] - old\n\n### Features\n\n"


def test_release_preserves_published_history_and_new_intro(tmp_path):
    previous = tmp_path / "old.md"
    changelog = tmp_path / "new.md"
    previous.write_text(PREVIOUS)
    changelog.write_text(GENERATED)
    assert module.main([str(previous), str(changelog), "--version", "v0.1.1", "--intro", "New introduction."]) == 0
    result = changelog.read_text()
    assert "New introduction.\n\n### Fixes\n\n- New fix." in result
    assert result[result.index("## [0.1.0]") :] == PREVIOUS[PREVIOUS.index("## [0.1.0]") :]


def test_squashed_release_keeps_all_published_sections_and_new_intro(tmp_path):
    published = (
        "## [0.9.2] - published\n\nPublished introduction.\n\n### Fixes\n\n"
        "- Build fix only in the release branch.\n- Regression fix only in the release branch.\n\n"
        + PREVIOUS[PREVIOUS.index("## [0.1.0]") :]
    )
    previous = tmp_path / "old.md"
    changelog = tmp_path / "new.md"
    previous.write_text("# Changelog\n\n" + published, encoding="utf-8")
    changelog.write_text(
        "# Changelog\n\n## [0.9.3] - new\n\n### Fixes\n\n- Current fix.\n\n"
        "## [0.9.2] - published\n\n### Fixes\n\n- Squash commit.\n",
        encoding="utf-8",
    )
    assert module.main([str(previous), str(changelog), "--version", "0.9.3", "--intro", "Current intro."]) == 0
    result = changelog.read_text(encoding="utf-8")
    assert "Current intro.\n\n### Fixes\n\n- Current fix." in result
    assert result[result.index("## [0.9.2]") :] == published
    assert "Squash commit." not in result


def test_history_is_preserved_even_when_generator_omits_an_old_release():
    assert module.preserve_history(GENERATED.split("## [0.1.0]", maxsplit=1)[0], PREVIOUS, "0.1.1").endswith(
        PREVIOUS[PREVIOUS.index("## [0.1.0]") :]
    )


@pytest.mark.parametrize(
    ("generated", "previous"),
    [
        (GENERATED.replace("[0.1.1]", "[wrong]"), PREVIOUS),
        (GENERATED + "\n## [0.1.1]\n", PREVIOUS),
        (GENERATED, PREVIOUS + "\n## [0.1.0]\n"),
        (GENERATED, PREVIOUS + "\n## [Unreleased]\n"),
    ],
)
def test_ambiguous_history_fails_closed(generated, previous):
    with pytest.raises(ValueError, match=r"changelog|release"):
        module.preserve_history(generated, previous, "0.1.1")


@pytest.mark.parametrize("previous", ["# Changelog\n", "# Changelog\n\n## [Unreleased]\n\n- Draft.\n"])
def test_first_release_does_not_duplicate_unreleased(previous):
    result = module.preserve_history(GENERATED.split("## [0.1.0]", maxsplit=1)[0], previous, "0.1.1")
    assert "## [0.1.1]" in result
    assert "Unreleased" not in result
    assert "Draft." not in result


def test_unreleased_before_published_sections_is_not_preserved():
    previous = "# Changelog\n\n## [Unreleased]\n\n- Draft.\n\n" + PREVIOUS[PREVIOUS.index("## [0.1.0]") :]
    result = module.preserve_history(GENERATED, previous, "0.1.1")
    assert "Unreleased" not in result
    assert result.endswith(PREVIOUS[PREVIOUS.index("## [0.1.0]") :])
