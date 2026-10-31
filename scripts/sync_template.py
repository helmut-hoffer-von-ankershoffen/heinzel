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
    # Hook runner is repository tooling, not a plugin test dependency or part of its runtime closure.
    text = text.replace("uv run --quiet prek install", "uv tool run --from prek==0.5.7 prek install")
    text = text.replace(" template-test template-sync docs attributions fuzz demo", "")
    text = text.replace(" release clean\n", " release clean update-template preview\n", 1)
    text = text.replace(" (KEEP=dir keeps it)", "")
    text = re.sub(r"\n(template-test|template-sync|docs|attributions|fuzz|demo|icons):.*?\n(?:\t.*\n)+", "\n", text)
    text = re.sub(r"(?m)^(\.PHONY:.*?) icons\b", r"\1", text)
    text = re.sub(r"\nlinux-user-manager-proof:.*?\n(?:\t.*\n)+", "\n", text)
    text = text.replace(" linux-user-manager-proof", "")
    # Core README e2e builds its own candidate wheel; plugins have no core web/notifier assets to prepare.
    text = (
        re.sub(r"\ne2e-prepare:.*?\n(?:\t.*\n)+", "\n", text)
        .replace(" test-e2e e2e-prepare ", " test-e2e ")
        .replace("test-e2e: e2e-prepare", "test-e2e:")
        .replace(
            '"e2e-readme=uv run pytest -m e2e -n 3 -q --session-timeout=600 -x '
            'tests/e2e/docs_test.py::test_readme_instructions" '
            '"e2e=uv run pytest -m e2e -n 3 -q --session-timeout=600 -x '
            '--deselect=tests/e2e/docs_test.py::test_readme_instructions"',
            '"e2e=uv run pytest -m e2e -n 3 -q --session-timeout=600 -x"',
        )
    )
    # The core's generate_docs.py writes the plugin pages with everything else; a plugin runs the builder (ADR 0082).
    text = text.replace(
        '"&docs=env -u HZ_SYSTEM_AGENT__SKILL_DIRS uv run python scripts/generate_docs.py --check"',
        '"&docs-plugins=uv run --quiet hz system plugin docs build --directory docs/plugins --check"',
    )
    text = text.replace(
        '"&docs=env -u HZ_SYSTEM_AGENT__SKILL_DIRS uv run python scripts/generate_docs.py"',
        '"&docs-plugins=uv run --quiet hz system plugin docs build --directory docs/plugins"',
    )
    # A plugin writes its pages with the builder, and has no README intro to record.
    text = text.replace(
        "docs-plugins: ## Write the page of every installed plugin into docs/plugins (ADR 0082); here part of `make "
        "docs`, which also writes the gallery\n"
        "\tenv -u HZ_SYSTEM_AGENT__SKILL_DIRS uv run python scripts/generate_docs.py",
        "docs-plugins: ## Write the page of every installed plugin into docs/plugins with "
        "`hz system plugin docs build` (ADR 0082)\n"
        "\tuv run --quiet hz system plugin docs build --directory docs/plugins",
    )
    text = text.replace(", and the README's intro; PLUGIN=name", "; PLUGIN=name")
    text = text.replace("scripts/docs_record.py --intro $(if", "scripts/docs_record.py $(if")
    # Steps only Heinzelmännchen has: import contracts, the template, demo tape, ATTRIBUTIONS.md (never a last step).
    text = re.sub(r'\n\t\t"&?(contracts|template|demo|attributions|docs-check)=[^\n]*', "", text)
    text = text.replace("uv build --all-packages", "uv build")
    text = text.replace("lint, import contracts, types, tests", "lint, types, tests")
    text = text.replace(
        "format, lint, contracts, generated files, unit tests", "format, lint, generated pages, unit tests"
    )
    text = text.replace("check with coverage, template, build", "check with coverage, build")
    text = text.replace("lint, architecture contracts and workflows", "lint and workflows")
    # Public product catalog and its browser suite are core website tooling, not plugin template dependencies.
    text = text.replace(" docs-catalog docs-site-check", "")
    text = re.sub(r"\n(docs-catalog|docs-site-check):.*?\n(?:\t.*\n)+", "\n", text)
    # The web head's app (web/, ADR 0039): plugins bring operations, not UI.
    text = text.replace(" web-build web-dev test-web", "").replace(" test-browser-deps", "")
    text = re.sub(r"\n# The web head's app in web/.*?\nWEB_TESTS := [^\n]*", "", text, flags=re.DOTALL)
    text = re.sub(r"\n(web-build|web-dev|test-web|test-browser-deps):.*?\n(?:\t.*\n)+", "\n", text)
    text = re.sub(r"\n\t\t\$\(WEB_LINT\) \$\(WEB_TYPES\) \\", "", text)
    text = re.sub(
        r" \$\(WEB_(LINT|TYPES|TESTS)\)"
        r"| \$\(subst &web-tests,web-tests,\$\(WEB_TESTS\)\)"
        r"|\n# Unit tests take every core[^\n]*",
        "",
        text,
    )
    text = re.sub(r'\n\t\t"&web-client=[^\n]*', "", text)
    text = re.sub(r"\n\t@\$\(WEB(_PROJECT)?\) [^\n]*", "", text)
    text = re.sub(r' "&web-vulnerabilities=[^"]*"', "", text)
    text = text.replace("@$(TOOLS) install build web", "@$(TOOLS) install")
    text = text.replace("build: web-build ## ", "build: ## ").replace("; the web app first)", ")")
    text = text.replace(" src/heinzel/heads/web/static", "")
    # Native process ownership belongs to the core; plugins carry neither its code nor build dependencies.
    text = text.replace(" managed-backend-setup managed-backend-check", "")
    text = re.sub(
        r"\n# The managed backend has no Electron dependency;.*?\nMANAGED_TYPES := [^\n]*", "", text, flags=re.DOTALL
    )
    text = re.sub(r" \$\(MANAGED_(CHECK|TYPES)\)", "", text)
    text = text.replace("\n\t@$(MAKE) --no-print-directory managed-backend-setup", "")
    text = re.sub(r"\n(managed-backend-setup|managed-backend-check):.*?\n(?:\t.*\n)+", "\n", text)
    # The native application is core-only; plugins do not carry the Electron window or Linux sandbox setup.
    electron_targets = {
        "desktop-electron-test",
        "desktop-electron-sandbox",
        "desktop-electron-native",
        "desktop-electron-package",
    }
    text = re.sub(
        r"(?m)^\.PHONY:.*$",
        lambda match: " ".join(name for name in match.group(0).split() if name not in electron_targets),
        text,
    )
    text = re.sub(r"(?m)^desktop-electron-(?:test|sandbox|native|package):[^\n]*\n(?:\t[^\n]*\n)*", "", text)
    # Heinzel Notifier, the core's macOS notifier app (ADR 0043): plugins have none.
    text = re.sub(r"(?m)^(\.PHONY:.*?) notifier\b", r"\1", text)
    text = re.sub(r"\nnotifier:.*?\n(?:\t.*\n)+", "\n", text)
    # The terminal art is drawn from the web app's drawing: plugins have none.
    text = re.sub(r"(?m)^(\.PHONY:.*?) terminal-art\b", r"\1", text)
    text = re.sub(r"\nterminal-art:.*?\n(?:\t.*\n)+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.replace(
        "Build the core wheel and sdist into dist/ (the web app first)",
        "Build this plugin's wheel and sdist into dist/",
    )
    text += (
        "\n.PHONY: docs\n"
        "\ndocs: docs-plugins ## Regenerate this plugin's documentation\n"
        "\npreview: ## Launch this plugin using isolated application state\n"
        "\tuv run --no-project --script scripts/plugin_preview.py\n"
    )
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
    text = text.replace(
        "\n      - name: Install browser runtimes and host dependencies\n        run: make test-browser-deps",
        "",
    )
    text = re.sub(r"\n      - run: make managed-backend-[^\n]*", "", text)
    # Only the core daemon uses the host's user service manager; plugins ship no such probe.
    text = re.sub(
        r"\n      - name: Inspect the Linux user manager without starting services\n.*?(?=\n      - )",
        "",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"\n      - name: Check the wheel ships the attributions\n[^\n]*", "", text)
    text = re.sub(r"\n      - name: Check the wheel ships the web app\n[^\n]*", "", text)
    text = text.replace("      - run: uv build --all-packages\n", "      - run: uv build\n")
    # Heinzel Notifier (ADR 0043): only the core builds and ships it.
    text = re.sub(r"\n  notifier:\n.*?(?=\n  build:)", "\n", text, flags=re.DOTALL)
    # The Electron window and its native runtime are core-only; plugins carry no desktop app.
    text = re.sub(r"\n  electron:\n.*?(?=\n  build:)", "\n", text, flags=re.DOTALL)
    text = re.sub(r"\n  changes:\n.*?(?=\n  dependency-review:)", "", text, flags=re.DOTALL)
    # The template has no changed-path job; keep its original E2E matrix policy without core-only gating.
    text = text.replace("    needs: changes\n", "")
    text = re.sub(r"(?m)^    if: .*needs\.changes\.outputs\.core[^\n]*\n", "", text)
    text = re.sub(
        r"(?m)^        os: .*needs\.changes\.outputs\.mounted_tailscale.*$",
        "        os: ${{ fromJSON(github.event_name == 'pull_request' && "
        '\'["ubuntu-latest"]\' || \'["ubuntu-latest","macos-latest"]\') }}',
        text,
    )
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
    text = text.replace(", notifier, electron, build]", ", build]")
    # Consumer compatibility belongs to core's public-contract release, never a plugin's own edit loop.
    text = re.sub(r"\n  consumer-(?:plan|check):\n.*?(?=\n  [a-z-]+:)", "\n", text, flags=re.DOTALL)
    text = re.sub(r"\n  local-plugin-(?:plan|check):\n.*?(?=\n  [a-z-]+:)", "\n", text, flags=re.DOTALL)
    text = re.sub(r"\n  core-wheel:\n.*?(?=\n  [a-z-]+:)", "\n", text, flags=re.DOTALL)
    text = text.replace(", consumer-plan, consumer-check]", "]")
    # Removed jobs must also disappear from the stable required aggregate, irrespective of list order.
    removed = {
        "changes",
        "template",
        "notifier",
        "electron",
        "consumer-plan",
        "consumer-check",
        "local-plugin-plan",
        "local-plugin-check",
        "core-wheel",
    }
    text = re.sub(
        r"(?m)^    needs: \[([^\n]+)\]$",
        lambda match: (
            "    needs: ["
            + ", ".join(name.strip() for name in match[1].split(",") if name.strip() not in removed)
            + "]"
        ),
        text,
    )
    # The core's CI covers every platform of the shared scripts/tools.toml; a plugin, possibly private and paying for
    text = text.replace("    needs: []\n", "")
    # its minutes, sets up on the two most contributors use.
    text = re.sub(
        r"(\n  setup:\n.*?\n        os: )\$\{\{ fromJSON[^\n]*",
        r"\1[ubuntu-latest, macos-latest]",
        text,
        count=1,
        flags=re.DOTALL,
    )
    text = re.sub(r"\n# GitHub-hosted macOS runners.*?\n(?=jobs:)", "\n", text, flags=re.DOTALL)
    return text.replace(", template, build]", ", build]").replace(", changes,", ",")


def pyproject() -> str:
    """The plugin's project metadata plus Heinzelmännchen's dev dependencies and tool configuration."""
    source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dev = source[source.index("[dependency-groups]") : source.index("[build-system]")]
    dev = re.sub(
        r"(?m)^test = \[[^\n]*\]$",
        'test = ["editables>=0.5", "coverage>=7.12", "httpx>=0.28", "pytest>=9.0.3,<10", '
        '"pytest-timeout>=2.4.0,<3", "pytest-xdist>=3.8"]',
        dev,
    )
    # Only Heinzelmännchen itself needs these: the template renderer, its import contracts, its first-party plugin and
    # the type stubs of its own dependencies.
    dev = re.sub(r'\n    "(copier|import-linter|heinzel-[a-z]+>=|types-croniter)[^\n]*', "", dev)
    dev = re.sub(r"\n    # The first-party plugins[^\n]*\n    #[^\n]*", "", dev)
    # Fuzzing targets Heinzelmännchen's contract parser (fuzz/).
    dev = re.sub(r"\n# Fuzzing[^\n]*\nfuzz = [^\n]*", "", dev)
    # The installed core artifact the plugin is developed against: a direct URL in the test group, never in
    # [tool.uv.sources], because uv applies a plugin's sources when `hz system plugin add` installs it from a path or
    # git and would fetch this Heinzelmännchen then.
    dev = dev.replace("test = [", 'test = ["heinzel==[[ heinzel_version ]]", ', 1)
    tools = source[source.index("[tool.ruff]") :]
    # This scheduling hint is implemented by the core test controller, not the plugin template.
    tools = tools.replace(
        '    "e2e_early: Hint to start a long E2E case in an early xdist worker batch; '
        'no deadline or selection change.",\n',
        "",
    )
    tools = re.sub(r'\nextend-exclude = \["template", "plugins"\][^\n]*', "", tools)
    tools = tools.replace(
        'extend-exclude = ["ATTRIBUTIONS.md", "site", "plugins"]', 'extend-exclude = ["ATTRIBUTIONS.md", "site"]'
    )
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
