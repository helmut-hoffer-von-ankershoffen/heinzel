"""Community registry entries cannot turn the generated gallery into unsafe links or paths."""

import importlib.util
import sys
from pathlib import Path

import pytest

from heinzel import HeinzelError
from heinzel.modules.system.plugin.docs._render import GENERATED

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/generate_docs.py"
spec = importlib.util.spec_from_file_location("generate_docs_registry", SCRIPT)
assert spec
assert spec.loader
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "../private"),
        ("name", "heinzel-X"),
        ("name", "other-plugin"),
        ("repository", "http://example.com/plugin"),
        ("repository", "https://user:password@example.com/plugin"),
        ("docs", "javascript:alert(1)"),
        ("docs", "https://example.com/a)evil"),
        ("docs", "https://example.com/\nlink"),
        ("docs", "https://["),
        ("repository", "https://example.com:bad/plugin"),
        ("repository", "https://example.com/plugin|injected-cell"),
    ],
)
def test_registry_rejects_unsafe_names_and_links(field, value, tmp_path, monkeypatch):
    entry = {"name": "heinzel-demo", "repository": "https://example.com/plugin", field: value}
    registry = tmp_path / "registry.toml"
    registry.write_text("", encoding="utf-8")
    monkeypatch.setattr(generator, "REGISTRY", registry)
    monkeypatch.setattr(generator.tomllib, "loads", lambda text: {"plugin": [entry]})
    with pytest.raises(ValueError, match="Plugin registry"):
        generator.plugin_gallery([])


def test_registry_summaries_remain_inert_plain_text_in_the_generated_site(tmp_path, monkeypatch):
    registry = tmp_path / "registry.toml"
    registry.write_text("", encoding="utf-8")
    monkeypatch.setattr(generator, "REGISTRY", registry)
    monkeypatch.setattr(
        generator.tomllib,
        "loads",
        lambda text: {
            "plugin": [
                {
                    "name": "heinzel-demo",
                    "repository": "https://example.com/plugin",
                    "summary": '<script>alert("x")</script> ![beacon](https://example.com/track)|cell',
                }
            ]
        },
    )
    page = generator.plugin_gallery([])
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "![beacon]" not in page
    assert "&#124;cell" in page


@pytest.mark.parametrize("linked", [False, True])
def test_core_generation_preserves_custom_pages_and_never_follows_output_links(tmp_path, monkeypatch, linked):
    plugins = tmp_path / "docs" / "plugins"
    plugins.mkdir(parents=True)
    protected = plugins / "README.md"
    original = "# Custom documentation\n"
    outside = tmp_path / "outside.md"
    if linked:
        outside.write_text(GENERATED + "\n# Owned elsewhere\n", encoding="utf-8")
        protected.symlink_to(outside)
    else:
        protected.write_text(original, encoding="utf-8")
    expected = protected.read_text(encoding="utf-8")
    monkeypatch.setattr(generator, "PLUGINS", plugins)
    monkeypatch.setattr(generator, "DOCS", tmp_path / "docs")
    template = tmp_path / "template"
    template.mkdir()
    (template / "README.md.jinja").write_text(
        generator.BADGES_START + "\n" + generator.BADGES_END + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(generator, "TEMPLATE", template)
    monkeypatch.setattr(generator, "BADGE_PAGES", ())
    monkeypatch.setattr(
        generator,
        "plugin_pages",
        lambda: {
            plugins / "heinzel-demo" / "index.md": GENERATED + "\n# Demo\n",
            protected: GENERATED + "\n# Gallery\n",
        },
    )
    for name in ("module_catalog", "cli_reference", "configuration_reference", "contract_schema", "template_security"):
        monkeypatch.setattr(generator, name, lambda: "generated\n")
    monkeypatch.setattr(generator, "model_schema", lambda *_args: "{}\n")
    monkeypatch.setattr(sys, "argv", ["generate_docs.py"])
    with pytest.raises(HeinzelError, match=r"symlink|Refusing to overwrite"):
        generator.main()
    assert protected.read_text(encoding="utf-8") == expected
    assert not (plugins / "heinzel-demo").exists()
    assert not (tmp_path / "docs" / "MODULES.md").exists()
