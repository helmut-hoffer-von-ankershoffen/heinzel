"""Public entry points share one unversioned quick start and PyPI never receives relative artwork."""

import re
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from markdown_it import MarkdownIt

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_github_pypi_and_pages_take_their_primary_command_from_one_source():
    expected = {
        "install": "uvx heinzel@latest",
        "install-script": "curl -LsSf https://helmut.hoffer-von-ankershoffen.me/heinzel/install.sh | sh",
    }
    guide = (ROOT / "docs/guides/install.md").read_text()
    for marker, value in expected.items():
        command = (ROOT / f"docs/_data/{marker}-command.txt").read_text().strip()
        assert command == value
        for name in ("README.md", "PYPI.md"):
            text = (ROOT / name).read_text()
            section = text.split(f"<!-- {marker}:start -->")[1].split(f"<!-- {marker}:end -->")[0]
            assert re.findall(r"```sh\n([^`]+)\n```", section) == [command]
        assert f"<!-- hz:{marker} -->" in guide
    site = (ROOT / "scripts/site_pages.py").read_text()
    assert '"install-command.txt"' in site
    assert '"install-script-command.txt"' in site
    assert (ROOT / "docs/install.sh").is_file()


def test_installation_surfaces_agree_on_supported_platforms():
    for name in ("README.md", "PYPI.md", "docs/guides/install.md"):
        text = (ROOT / name).read_text()
        assert "**macOS and Linux**" in text
        assert "Windows may work, but is not supported." in text


def test_published_description_has_only_portable_links_and_real_raster_artwork():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    text = (ROOT / metadata["project"]["readme"]).read_text()
    assert text.startswith("# Heinzelmännchen\n")
    images = []
    for block in MarkdownIt().parse(text):
        for token in block.children or []:
            if token.type in {"image", "link_open"}:
                value = token.attrGet("src" if token.type == "image" else "href")
                assert value
                assert urlsplit(value).scheme == "https", value
                if token.type == "image":
                    images.append(value)
        if block.type == "html_block":
            assert block.content.lstrip().startswith("<!--"), "PyPI content must not rely on repository HTML"
    assert images
    prefix = "https://raw.githubusercontent.com/helmut-hoffer-von-ankershoffen/heinzel/main/"
    for url in images:
        assert url.startswith(prefix)
        local = ROOT / url.removeprefix(prefix)
        assert local.suffix in {".png", ".jpg", ".jpeg", ".webp"}
        assert local.read_bytes().startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8", b"RIFF"))
