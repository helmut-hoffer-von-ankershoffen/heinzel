# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
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
    text = text.replace(" template-test template-sync docs attributions fuzz demo", "")
    text = text.replace(" release clean\n", " release clean update-template\n", 1)
    text = text.replace(" (KEEP=dir keeps it)", "")
    text = re.sub(r"\n(template-test|template-sync|docs|attributions|fuzz|demo):.*?\n(?:\t.*\n)+", "\n", text)
    # Steps only Heinzelmännchen has: import contracts, the template, generated docs, demo tape, ATTRIBUTIONS.md
    # (never a last step).
    text = re.sub(r'\n\t\t"&?(contracts|template|docs|demo|attributions)=[^\n]*', "", text)
    text = text.replace("uv build --all-packages", "uv build")
    text = text.replace("lint, import contracts, types, tests", "lint, types, tests")
    text = text.replace("format, lint, contracts, generated files, unit tests", "format, lint, unit tests")
    text = text.replace("check with coverage, template, build", "check with coverage, build")
    text = text.replace("lint, architecture contracts and workflows", "lint and workflows")
    # The web head's app (web/, ADR 0039): plugins bring operations, not UI.
    text = text.replace(" web-build web-dev test-web", "")
    text = re.sub(r"\n# The web head's app in web/.*?\nWEB_TESTS := [^\n]*", "", text, flags=re.DOTALL)
    text = re.sub(r"\n(web-build|web-dev|test-web):.*?\n(?:\t.*\n)+", "\n", text)
    text = re.sub(r"\n\t\t\$\(WEB_LINT\) \$\(WEB_TYPES\) \\", "", text)
    text = re.sub(r" \$\(WEB_(LINT|TYPES|TESTS)\)", "", text)
    text = re.sub(r'\n\t\t"&web-client=[^\n]*', "", text)
    text = re.sub(r"\n\t@\$\(WEB(_PROJECT)?\) [^\n]*", "", text)
    text = re.sub(r' "&web-vulnerabilities=[^"]*"', "", text)
    text = text.replace("@$(TOOLS) install build web", "@$(TOOLS) install")
    text = text.replace("build: web-build ## ", "build: ## ").replace("; the web app first)", ")")
    text = text.replace(" src/heinzel/heads/web/static", "")
    # Heinzel Notifier, the core's macOS notifier app (ADR 0043): plugins have none.
    text = re.sub(r"(?m)^(\.PHONY:.*?) notifier\b", r"\1", text)
    text = re.sub(r"\nnotifier:.*?\n(?:\t.*\n)+", "\n", text)
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
    # The web head's app (ADR 0039) and ATTRIBUTIONS.md, the notices of what it bundles: plugins have neither.
    text = re.sub(r"\n      - run: make test-web[^\n]*", "", text)
    text = re.sub(r"\n      - name: Check the wheel ships the attributions\n[^\n]*", "", text)
    text = re.sub(r"\n      - name: Check the wheel ships the web app\n[^\n]*", "", text)
    text = text.replace("      - run: uv build --all-packages\n", "      - run: uv build\n")
    # Heinzel Notifier (ADR 0043): only the core builds and ships it.
    text = re.sub(r"\n  notifier:\n.*?(?=\n  build:)", "\n", text, flags=re.DOTALL)
    text = re.sub(r"\n  changes:\n.*?(?=\n  dependency-review:)", "", text, flags=re.DOTALL)
    text = text.replace("    needs: notifier\n", "")
    text = re.sub(r"    if: \$\{\{ !cancelled\(\) && needs\.notifier\.result[^\n]*\n", "", text)
    text = re.sub(
        r"\n      - if: needs\.notifier[^\n]*\n        uses: actions/download-artifact@[^\n]*"
        r"\n        with:\n          name: notifier",
        "",
        text,
    )
    text = re.sub(r"\n      - name: Unpack Heinzel Notifier into the package\n[^\n]*\n[^\n]*", "", text)
    text = re.sub(r"\n      - name: Check the wheel ships Heinzel Notifier\n[^\n]*\n[^\n]*", "", text)
    text = text.replace(", notifier, build]", ", build]")
    # The core's CI covers every platform of the shared scripts/tools.toml; a plugin, possibly private and paying for
    # its minutes, sets up on the two most contributors use.
    text = re.sub(
        r"(    name: make setup[^\n]*\n(?:[^\n]*\n)*?        os: )\$\{\{ fromJSON[^\n]*",
        r"\1[ubuntu-latest, macos-latest]",
        text,
    )
    return text.replace(", template, build]", ", build]").replace(", changes,", ",")


def pyproject() -> str:
    """The plugin's project metadata plus Heinzelmännchen's dev dependencies and tool configuration."""
    source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dev = source[source.index("[dependency-groups]") : source.index("[build-system]")]
    # Only Heinzelmännchen itself needs these: the template renderer, its import contracts, its first-party plugin and
    # the type stubs of its own dependencies.
    dev = re.sub(r'\n    "(copier|import-linter|heinzel-[a-z]+>=|types-croniter)[^\n]*', "", dev)
    dev = re.sub(r"\n    # The first-party plugins[^\n]*\n    #[^\n]*", "", dev)
    # Fuzzing targets Heinzelmännchen's contract parser (fuzz/).
    dev = re.sub(r"\n# Fuzzing[^\n]*\nfuzz = [^\n]*", "", dev)
    # The Heinzelmännchen the plugin is developed against: a direct URL in the test group, never in
    # [tool.uv.sources], because uv applies a plugin's sources when `hz system plugin add` installs it from a path or
    # git and would fetch this Heinzelmännchen then.
    dev = dev.replace(
        "test = [\n",
        'test = [\n    "heinzel @ git+https://github.com/[[ heinzel_owner ]]/heinzel@v[[ heinzel_version ]]",\n',
    )
    tools = source[source.index("[tool.ruff]") :]
    tools = re.sub(r'\nextend-exclude = \["template"\][^\n]*', "", tools)
    tools = re.sub(r'\n"fuzz/\*\.py" = \[\n[^\]]*\]', "", tools)
    tools = re.sub(r'\n"hatch_build\.py" = \[\n[^\]]*\]', "", tools)  # the web head's build hook
    tools = re.sub(r'\n\[\[tool\.mypy\.overrides\]\]\nmodule = \["atheris"\][^\n]*\n[^\n]*\n', "", tools)
    tools = re.sub(r"\n\[tool\.importlinter\].*?(?=\n\[tool\.(?!importlinter)|\Z)", "\n", tools, flags=re.DOTALL)
    tools = re.sub(r'source = \["heinzel"(, "heinzel_\w+")*\]', 'source = ["[[ package_name ]]"]', tools)
    # The workspace (plugins/) exists only here.
    tools = re.sub(r"\nmypy_path = [^\n]*", "", tools)
    tools = tools.replace('files = ["src", "plugins/*/src"]', 'files = ["src"]')
    tools = tools.replace('include = ["src", "plugins/*/src"]', 'include = ["src"]')
    tools = tools.replace('testpaths = ["tests", "plugins"]', 'testpaths = ["tests"]')
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
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    # Without the web head's app (ADR 0039): plugins have no web/.
    core_only = (
        r"node_modules/|src/heinzel/heads/web/static/|src/heinzel/modules/system/notify/macos/\S+|web/test-results/"
    )
    pattern = rf"(?m)^({core_only})\n"
    return re.sub(pattern, "", text)


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
