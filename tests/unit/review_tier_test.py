import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("review_tier", REPO / "scripts" / "review_tier.py")
assert spec
assert spec.loader
review_tier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review_tier)


@pytest.mark.parametrize(
    "path",
    [
        "src/heinzel/kernel/_installation.py",
        "src/heinzel/kernel/_installation_continuity.py",
        "src/heinzel/kernel/_installed_metadata.py",
        "src/heinzel/kernel/_metadata_verifier.py",
        "src/heinzel/heads/install.py",
        "src/heinzel/heads/_tool_install.py",
        "src/heinzel/heads/bootstrap.py",
        "src/heinzel/heads/cli/_update.py",
        "src/heinzel/heads/web/_updates.py",
        "src/heinzel/modules/system/_update.py",
        "src/heinzel/modules/system/lifecycle/_uninstall.py",
        "src/heinzel/modules/system/daemon/_service.py",
        "docs/install.sh",
        "docs/_data/install-command.txt",
        "agent-skills/skills/hz-init/scripts/install.sh",
        "src/heinzel/kernel/_credentials.py",
        "src/heinzel/modules/system/credential/_service.py",
        "src/heinzel/heads/api/_auth.py",
        "src/heinzel/heads/web/_pairing.py",
        "src/heinzel/kernel/_api_access.py",
        "src/heinzel/modules/ai/access/_gateway.py",
        "src/heinzel/kernel/_approval.py",
        "src/heinzel/modules/heinzel/_connected_read_set_approvals.py",
        "src/heinzel/modules/heinzel/_model_grants.py",
        "src/heinzel/modules/heinzel/_model_admission.py",
        "src/heinzel/modules/heinzel/_turn_authority_fence.py",
        "src/heinzel/kernel/_release.py",
        "scripts/release.sh",
        "scripts/pypi_release.py",
        ".github/workflows/ci.yml",
        ".github/workflows/source-review.yml",
        ".github/AGENTS.md",
        "template/.github/workflows/publish.yml",
        "starter/[% if layout == 'standalone' %].github[% endif %]/workflows/ci.yml.jinja",
        "pyproject.toml",
        "uv.lock",
        "Makefile",
        "hatch_build.py",
        ".claude/settings.json",
        "scripts/source_review.py",
        "docs/plugins/registry.toml",
        "docs/catalog-sources.toml",
        "UNKNOWN.bin",
        "web/src/api/token.ts",
    ],
)
def test_risk_areas_are_full(path):
    assert review_tier.tier_of([path]) == "full", review_tier.decide(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/heinzel/kernel/_paths.py",
        "src/heinzel/modules/graph/_service.py",
        "src/heinzel/modules/system/theme/__init__.py",
        "web/src/pages/workflow/model.ts",
        "desktop/src/main.ts",
        "scripts/ci_logs.py",
        "AGENTS.md",
        "src/heinzel/kernel/AGENTS.md",
        "src/heinzel/modules/system/skill/skills/explain-heinzel/SKILL.md",
    ],
)
def test_other_code_is_light(path):
    assert review_tier.tier_of([path]) == "light", review_tier.decide(path)


@pytest.mark.parametrize(
    "path",
    [
        "docs/ARCHITECTURE.md",
        "docs/adr/0114-green-main-releases-to-edge-and-acceptance-promotes-to-stable.md",
        "docs/assets/tour/screenshot.png",
        "tests/unit/paths_test.py",
        "tests/unit/credential_test.py",
        "tests/e2e/install_test.py",
        "README.md",
        "CONTRIBUTING.md",
        "template/docs/README.md",
    ],
)
def test_documentation_tests_and_markdown_need_no_review(path):
    assert review_tier.tier_of([path]) == "none", review_tier.decide(path)


def test_a_mixed_change_takes_its_highest_tier():
    assert review_tier.tier_of(["docs/a.md", "tests/unit/a_test.py"]) == "none"
    assert review_tier.tier_of(["docs/a.md", "src/heinzel/kernel/_paths.py"]) == "light"
    assert review_tier.tier_of(["docs/a.md", "src/heinzel/kernel/_paths.py", "uv.lock"]) == "full"
    assert review_tier.tier_of(["uv.lock", "docs/a.md"]) == "full"


def test_unknown_and_malformed_paths_are_full_and_an_empty_change_is_not_none():
    assert review_tier.tier_of(["docs/a.md", "brand-new-top-level/file.txt"]) == "full"
    for bad in ("", "/etc/passwd", "../outside.md", "docs/../src/x.py", "docs//a.md", "docs/a\nb.md"):
        assert review_tier.decide(bad).tier == "full", bad
    assert review_tier.tier_of([]) == "full"


def test_case_cannot_lower_a_tier():
    assert review_tier.tier_of(["Tests/unit/a_test.py"]) == "full"  # not tests/: unrecognized
    assert review_tier.tier_of(["docs/INSTALL.SH"]) == "full"
    assert review_tier.tier_of(["src/heinzel/kernel/_Credentials.py"]) == "full"


def test_markdown_that_is_shipped_or_run_is_not_none():
    assert review_tier.decide("src/heinzel/modules/graph/templates/team-graph-keeper.prose.md").tier == "light"
    assert review_tier.decide(".claude/skills/review/SKILL.md").tier == "full"
    assert review_tier.decide("agent-skills/skills/hz/SKILL.md").tier == "full"


def test_a_deleted_path_counts():
    paths = review_tier.parse_status("M\tdocs/a.md\nD\tsrc/heinzel/kernel/_installation.py\n")
    assert paths == ["docs/a.md", "src/heinzel/kernel/_installation.py"]
    assert review_tier.tier_of(paths) == "full"


def test_a_rename_counts_on_both_sides():
    out_of_a_risk_area = review_tier.parse_status("R087\tsrc/heinzel/kernel/_approval.py\tdocs/old-approval.md\n")
    into_a_risk_area = review_tier.parse_status("R100\tdocs/draft.md\tsrc/heinzel/kernel/_credentials_store.py\n")
    assert out_of_a_risk_area == ["src/heinzel/kernel/_approval.py", "docs/old-approval.md"]
    assert review_tier.tier_of(out_of_a_risk_area) == "full"
    assert review_tier.tier_of(into_a_risk_area) == "full"
    assert review_tier.tier_of(["docs/old-approval.md"]) == "none"  # the new name alone would have hidden it


def test_json_lines_carry_renames_from_the_pull_request_files_api():
    paths = review_tier.parse_json_lines('["docs/new.md", "scripts/release.sh"]\n"docs/a.md"\n')
    assert paths == ["docs/new.md", "scripts/release.sh", "docs/a.md"]
    assert review_tier.tier_of(paths) == "full"


@pytest.mark.parametrize("text", ["\tdocs/a.md", "M", "M\t"])
def test_malformed_status_lines_are_refused(text):
    with pytest.raises(ValueError, match="name-status"):
        review_tier.parse_status(text)


def test_malformed_json_lines_are_refused():
    with pytest.raises(ValueError, match="path"):
        review_tier.parse_json_lines("12\n")


def test_command_prints_the_tier_and_why(capsys, monkeypatch):
    assert review_tier.main(["--why", "docs/a.md", "uv.lock"]) == 0
    out = capsys.readouterr()
    assert out.out == "full\n"
    assert "uv.lock" in out.err
    assert "docs/a.md" not in out.err


def test_command_without_paths_is_an_error(capsys, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert review_tier.main([]) == 2
    assert "no paths" in capsys.readouterr().err
