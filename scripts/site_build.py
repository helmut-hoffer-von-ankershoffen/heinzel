# /// script
# requires-python = ">=3.12"
# dependencies = ["mkdocs-material==9.8.0", "packaging==26.3"]
# ///
"""Build or serve Markdown in an isolated documentation environment, without installing core or providers."""

from mkdocs.__main__ import cli

if __name__ == "__main__":
    cli()
