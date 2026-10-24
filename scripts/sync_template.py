"""Derive the plugin template's Makefile, CI workflow and tool configuration from ours, so they never drift.

Files that are identical for Heinzelmännchen and its plugins are symlinks in template/; files that differ slightly
are generated here. Run `make template-sync` after changing Makefile, .github/workflows/ci.yml or pyproject.toml;
`make lint` fails if the template is out of date.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "template"
PYPROJECT_HEAD = ROOT / "scripts" / "template_pyproject_head.toml.tmpl"


def makefile() -> str:
    """The Makefile without Heinzelmännchen-only targets, plus `update-template`."""
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    text = text.replace(" template-test template-sync docs", "")
    text = text.replace(" release clean\n", " release clean update-template\n", 1)
    text = text.replace(" (KEEP=dir keeps it)", "")
    text = re.sub(r"\n(template-test|template-sync|docs):.*?\n(?:\t.*\n)+", "\n", text)
    # Steps only Heinzelmännchen has: import contracts, the template and generated docs (never a target's last step).
    text = re.sub(r'\n\t\t"&?(contracts|template|docs)=[^\n]*', "", text)
    text = text.replace("uv build --all-packages", "uv build")
    text = text.replace("lint, import contracts, types, tests", "lint, types, tests")
    text = text.replace("format, lint, contracts, generated files, unit tests", "format, lint, unit tests")
    text = text.replace("check with coverage, template, build", "check with coverage, build")
    text = text.replace("lint, architecture contracts and workflows", "lint and workflows")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text + (
        "\nupdate-template: ## Update this plugin to the latest Heinzelmännchen template (needs a clean working tree)\n"
        "\tuvx copier update --defaults\n"
    )


def ci_workflow() -> str:
    """The CI workflow without the template job; the build check looks for the plugin package."""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    text = re.sub(r"\n  template:\n.*?(?=\n  build:)", "\n", text, flags=re.DOTALL)
    text = re.sub(
        r"\n      - name: Require the confidential terms\n.*?(?=\n      - run: make lint)", "", text, flags=re.DOTALL
    )
    text = text.replace(
        "        run: unzip -l dist/heinzel-[0-9]*.whl | grep -q 'heinzel/CHANGELOG.md'",
        "        run: unzip -l dist/*.whl | grep -q '[[ package_name ]]/__init__.py'",
    )
    text = text.replace("- name: Check the wheel ships the changelog", "- name: Check the wheel ships the package")
    text = text.replace("      - run: uv build --all-packages\n", "      - run: uv build\n")
    return text.replace(", template, build]", ", build]")


def pyproject() -> str:
    """The plugin's project metadata plus Heinzelmännchen's dev dependencies and tool configuration."""
    source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dev = source[source.index("[dependency-groups]") : source.index("[build-system]")]
    dev = re.sub(r'\n    "(copier|import-linter|heinzel-clock)[^\n]*', "", dev)
    # The Heinzelmännchen the plugin is developed against: a direct URL in the test group, never in
    # [tool.uv.sources], because uv applies a plugin's sources when `hz system plugin add` installs it from a path or
    # git and would fetch this Heinzelmännchen then.
    dev = dev.replace(
        "test = [\n",
        'test = [\n    "heinzel @ git+https://github.com/[[ heinzel_owner ]]/heinzel@v[[ heinzel_version ]]",\n',
    )
    tools = source[source.index("[tool.ruff]") :]
    tools = re.sub(r'\nextend-exclude = \["template"\][^\n]*', "", tools)
    tools = re.sub(r"\n\[tool\.importlinter\].*?(?=\n\[tool\.(?!importlinter)|\Z)", "\n", tools, flags=re.DOTALL)
    tools = tools.replace('source = ["heinzel", "heinzel_clock"]', 'source = ["[[ package_name ]]"]')
    # The workspace (packages/) exists only here.
    tools = re.sub(r"\nmypy_path = [^\n]*", "", tools)
    tools = tools.replace('files = ["src", "packages/*/src"]', 'files = ["src"]')
    tools = tools.replace('include = ["src", "packages/*/src"]', 'include = ["src"]')
    tools = tools.replace('testpaths = ["tests", "packages"]', 'testpaths = ["tests"]')
    # A new plugin starts at 0.0.0, like its [project] version, not at Heinzelmännchen's version.
    tools = re.sub(r'(\[tool\.bumpversion\]\ncurrent_version = )"[^"]*"', r'\1"0.0.0"', tools)
    start, end = tools.index("[[tool.bumpversion.files]]"), tools.index("[tool.git-cliff.remote.github]")
    tools = (
        tools[:start]
        + '[[ "[[" ]]tool.bumpversion.files]]\nfilename = "pyproject.toml"\n'
        + 'search = "name = \\"[[ project_name ]]\\"\\nversion = \\"{current_version}\\""\n'
        + 'replace = "name = \\"[[ project_name ]]\\"\\nversion = \\"{new_version}\\""\n\n'
        + tools[end:]
    )
    tools = tools.replace(
        'owner = "helmut-hoffer-von-ankershoffen"\nrepo = "heinzel"',
        'owner = "[[ github_owner ]]"\nrepo = "[[ project_name ]]"',
    )
    # A plugin's commit scopes are its own module and area names.
    tools = re.sub(r"\\\(\(kernel\|[a-z|-]+\)\\\)", r"\\([a-z0-9-]+\\)", tools)
    tools = tools.replace("All notable changes to Heinzelmännchen,", "All notable changes to this plugin,")
    tools = tools.replace(
        "https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/",
        "https://github.com/[[ github_owner ]]/[[ project_name ]]/pull/",
    )
    tools = tools.replace("[#", '[[ "[#" ]]')  # [# starts a comment in the template's Jinja dialect
    head = PYPROJECT_HEAD.read_text(encoding="utf-8")
    return head.replace("# DEPENDENCY-GROUPS\n", dev).rstrip() + "\n\n" + tools.rstrip() + "\n"


def gitignore() -> str:
    """Git won't read a symlinked .gitignore, so the template gets a copy."""
    return (ROOT / ".gitignore").read_text(encoding="utf-8")


GENERATED = {
    TEMPLATE / ".gitignore": gitignore,
    TEMPLATE / "Makefile": makefile,
    TEMPLATE / ".github" / "workflows" / "ci.yml.jinja": ci_workflow,
    TEMPLATE / "pyproject.toml.jinja": pyproject,
}


def main() -> int:
    """Write the derived files, or with --check only report the stale ones."""
    check = "--check" in sys.argv
    stale: list[Path] = []
    for path, render in GENERATED.items():
        content = render()
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != content:
            stale.append(path.relative_to(ROOT))
            if not check:
                path.write_text(content, encoding="utf-8")
    if check and stale:
        print("The template is out of date; run `make template-sync`:", *stale, sep="\n  ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
