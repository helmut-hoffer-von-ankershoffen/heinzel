"""The product site lives on heinzel.team; the owner's personal domain must not creep back into the repository."""

import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SITE = "https://heinzel.team/"
# Built from parts so this file does not contain the string it forbids.
OLD_DOMAIN = "hoffer-von-ankershoffen" + ".me"
# The imprint links the provider's own website on the owner's request; it is not a link to the product site.
PERSONAL_LINK_ALLOWED = {"docs/imprint.md"}


def _tracked_text_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    return [REPO / name for name in listed.split("\0") if name and (REPO / name).is_file()]


def test_no_tracked_file_points_at_the_old_site_domain() -> None:
    """Released 0.9.9 and its installs still use the old URLs, and GitHub redirects them; new text must not."""
    offenders = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary files
        name = str(path.relative_to(REPO))
        if OLD_DOMAIN in text and name not in PERSONAL_LINK_ALLOWED:
            offenders.append(name)
    assert not offenders, f"These files still use the old site domain; use {SITE}: {offenders}"


def test_site_url_and_project_urls_use_the_product_domain() -> None:
    """The docs build, the package metadata and the install command agree on one domain."""
    mkdocs = (REPO / "mkdocs.yml").read_text(encoding="utf-8")
    assert f"site_url: {SITE}\n" in mkdocs
    urls = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["urls"]
    assert urls["Homepage"] == SITE
    assert urls["Documentation"].startswith(SITE)
    command = (REPO / "docs/_data/install-script-command.txt").read_text(encoding="utf-8").strip()
    assert command == f"curl -LsSf {SITE}install.sh | sh"


def test_robots_txt_points_at_the_sitemap_on_the_product_domain() -> None:
    robots = (REPO / "docs/robots.txt").read_text(encoding="utf-8")
    assert f"Sitemap: {SITE}sitemap.xml" in robots
    assert "Disallow: /\n" not in robots


def test_link_previews_use_an_existing_image_and_the_landing_pages_describe_themselves() -> None:
    template = (REPO / "docs/overrides/main.html").read_text(encoding="utf-8")
    assert 'config.site_url ~ "assets/pypi-hero.png"' in template
    assert (REPO / "docs/assets/pypi-hero.png").is_file()
    for name in ("og:title", "og:description", "og:image", "twitter:card"):
        assert name in template
    docs = REPO / "docs"
    descriptions = {}
    for path in sorted(docs.rglob("*.md")):
        source = path.read_text(encoding="utf-8")
        if not source.startswith("---\n"):
            continue
        front, separator, _ = source[4:].partition("\n---\n")
        if not any(line.startswith("description:") for line in front.splitlines()):
            continue
        page = path.relative_to(docs).as_posix()
        assert separator, f"{page} is missing its closing metadata delimiter"
        try:
            metadata = yaml.safe_load(front)
        except yaml.YAMLError as error:
            pytest.fail(f"{page} has invalid YAML front matter: {error}")
        assert isinstance(metadata, dict), page
        description = metadata.get("description")
        assert isinstance(description, str), page
        assert description.strip(), page
        descriptions[page] = description.strip()
    for page in ("README", "CATALOG", "guides/install", "guides/tour", "guides/manager", "guides/joining"):
        assert 50 <= len(descriptions.get(f"{page}.md", "")) <= 170, page
