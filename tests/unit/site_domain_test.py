"""The product site lives on heinzel.team; the owner's personal domain must not creep back into the repository."""

import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SITE = "https://heinzel.team/"
# Built from parts so this file does not contain the string it forbids.
OLD_DOMAIN = "hoffer-von-ankershoffen" + ".me"


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
        if OLD_DOMAIN in text:
            offenders.append(str(path.relative_to(REPO)))
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
    for page in ("README", "CATALOG", "guides/install", "guides/tour", "guides/manager", "guides/joining"):
        front = (REPO / f"docs/{page}.md").read_text(encoding="utf-8").split("---")[1]
        description = next(line for line in front.splitlines() if line.startswith("description:")).split(":", 1)[1]
        assert 50 <= len(description.strip()) <= 170, page
