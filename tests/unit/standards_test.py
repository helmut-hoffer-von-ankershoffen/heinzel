"""Standards the maintainers set that no off-the-shelf linter checks (skill `codify` adds them here).

Each check comes with a test that feeds it a violation, so a check that silently stops matching fails too.
"""

import ast
import importlib.util
import re
import subprocess
import tomllib
from pathlib import Path
from types import ModuleType
from typing import get_origin

import pytest
import yaml
from packaging.requirements import Requirement

from heinzel import Kernel, Module, OperationLet
from heinzel.kernel import env_prefix

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "template"
CODE = [*REPO.glob("src/**/*.py"), *REPO.glob("scripts/*.py"),
        *REPO.glob("tests/**/*.py"), *REPO.glob(".claude/hooks/*.py"),
        *REPO.glob("template/**/*.py"), *REPO.glob("template/**/*.py.jinja")]  # fmt: skip
BLOCKING = {"run", "check_call", "check_output", "call"}
EXEMPT = "interactive: no timeout"
PLACEHOLDER = re.compile(r"\[\[.*?\]\]|\[%.*?%\]")  # the template's copier variables and blocks


def python_source(path: Path) -> str:
    """A Python file's source; a template's placeholders become an identifier, so it parses."""
    source = path.read_text(encoding="utf-8")
    return PLACEHOLDER.sub("placeholder", source) if path.suffix == ".jinja" else source


def test_template_placeholders_become_identifiers(tmp_path):
    path = tmp_path / "x.py.jinja"
    path.write_text('from [[ package_name ]] import a\nURL = "x/[[ owner ]]/y"\n[% if x %]\n', encoding="utf-8")
    assert python_source(path) == 'from placeholder import a\nURL = "x/placeholder/y"\nplaceholder\n'


def calls_without_timeout(source: str) -> list[int]:
    """Lines of `subprocess.<blocking>(...)` calls that pass no `timeout=` and aren't marked as interactive."""
    lines = source.splitlines()
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in BLOCKING
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and not any(keyword.arg in {"timeout", None} for keyword in node.keywords)
        and EXEMPT not in lines[node.lineno - 1]
    ]


def test_finds_calls_without_timeout():
    source = "import subprocess\nsubprocess.run(['ls'])\nsubprocess.run(['ls'], timeout=5)\nsubprocess.run(**kw)\n"
    assert calls_without_timeout(source) == [2]


@pytest.mark.parametrize("path", CODE, ids=lambda p: str(p.relative_to(REPO)))
def test_every_subprocess_call_has_a_timeout(path):
    """Nothing may hang forever on a stuck command; interactive commands say so on the call's line."""
    assert calls_without_timeout(python_source(path)) == [], f"add timeout= or `# {EXEMPT}`"


def repository_files() -> list[str]:
    """Paths of the files git tracks or would track (not ignored ones), relative to the repository."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.splitlines()
    return [path for path in listed if not path.startswith("plugins/") and (REPO / path).is_file()]


# Template parity: plugins get the same agents, hooks, skills, workflows and scripts, unless they are core-only.

SHARED = [".claude", ".github/workflows", "scripts"]
CORE_ONLY = {  # path: why plugins don't get it
    "scripts/site_characters.mjs": "draws product-site guides from core artwork; standalone plugins own their art",
    "scripts/site_catalog.py": "public product website aggregation, never a standalone plugin build (ADR 0098)",
    "scripts/site_pages.py": "renders the product's public ecosystem catalog, not plugin-owned docs (ADR 0098)",
    "scripts/site_check.py": "accepts the core product website with its locked web axe tooling (ADR 0098)",
    ".github/workflows/pypi.yml": "publishes only the registered core project with its trusted publisher (ADR 0079)",
    "scripts/pypi_release.py": "verifies core-only PyPI identity and browser/notifier payloads (ADR 0079)",
    ".github/workflows/fuzz.yml": "fuzzes Heinzelmännchen's contract parser (fuzz/); plugins have no fuzz targets",
    ".claude/audit/evals.yaml": "cases from Heinzelmännchen's history; a plugin's self-audit skips self-improvement",
    ".claude/audit/ledger.jsonl": "Heinzelmännchen's own audit record; skill self-audit starts a plugin's empty",
    ".claude/skills/codify/SKILL.md": "adds checks to Heinzelmännchen's standards_test.py; plugins inherit them",
    ".claude/skills/raise-the-bar/SKILL.md": "audits Heinzelmännchen's own areas before a release",
    ".claude/skills/scaffold-plugin/SKILL.md": "creates plugins; a plugin updates itself with update-template",
    "scripts/attributions.py": "writes ATTRIBUTIONS.md of the core and its web app; plugins bundle nothing",
    "scripts/demo_tape.py": "assembles the README demo from the core intro and its modules' snippets",
    "scripts/generate_docs.py": "generates Heinzelmännchen's CLI, module and configuration reference",
    "scripts/notifier.py": "builds Heinzel Notifier, the core's macOS notifier app (ADR 0043); plugins have none",
    "scripts/terminal_art.py": "draws Heinzelmännchen for the terminal from the web app's drawing; plugins bring none",
    "scripts/sync_template.py": "generates the template from Heinzelmännchen's files",
    "scripts/template-test.sh": "renders and checks the template",
    "scripts/starter-test.py": "core SDK compatibility job exercises fresh standalone and collection authors",
    "scripts/scoped_checks.py": "routes Core-root checks; owning and governed plugins already check their own project",
    "scripts/template_pyproject_head.toml.tmpl": "input of sync_template.py",
    "scripts/web.py": "the web head's typed client and dev server (web/, ADR 0039); plugins bring no UI",
    "scripts/web_build.py": "runs and builds the web head's app (web/, ADR 0039); plugins bring no UI",
}


def _load(path: Path) -> ModuleType:
    """A script that isn't on the import path, as a module."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _generated() -> set[Path]:
    """The template files scripts/sync_template.py derives from Heinzelmännchen's."""
    return set(_load(REPO / "scripts" / "sync_template.py").GENERATED)


GENERATED = _generated()
SHARED_FILES = sorted(path for path in repository_files() if path.startswith(tuple(f"{d}/" for d in SHARED)))


def parity_problem(path: str, generated: set[Path]) -> str | None:
    """Why the template lacks a shared file, or has one that is core-only; None when it is right."""
    counterpart = TEMPLATE / path
    jinja = counterpart.with_name(counterpart.name + ".jinja")
    if path in CORE_ONLY:
        present = counterpart.exists() or jinja.exists() or counterpart in generated
        return "core-only, but in the template" if present else None
    if counterpart.is_symlink():
        return None if counterpart.resolve() == (REPO / path).resolve() else "symlink points elsewhere"
    if counterpart in generated or jinja.exists():
        return None
    return "symlink it into template/ (or generate it), or add it to CORE_ONLY with the reason"


def test_finds_parity_problems():
    assert parity_problem(".claude/hooks/missing_hook.py", set()) is not None
    assert parity_problem("scripts/sync_template.py", set()) is None
    assert parity_problem("scripts/sync_template.py", {TEMPLATE / "scripts/sync_template.py"}) is not None
    assert parity_problem(".claude/skills/release/SKILL.md", set()) is None  # rendered from SKILL.md.jinja


@pytest.mark.parametrize("path", SHARED_FILES)
def test_shared_files_reach_plugins_through_the_template(path):
    """A scaffolded plugin ships every agent, hook, skill, workflow and script, or CORE_ONLY says why not."""
    assert parity_problem(path, GENERATED) is None, parity_problem(path, GENERATED)


def test_core_only_files_exist():
    assert [path for path in CORE_ONLY if not (REPO / path).is_file()] == [], "remove stale CORE_ONLY entries"


def test_tests_may_use_fake_tokens_in_heinzel_and_in_plugins():
    """Plugin tests hand fake tokens to adapters; a plugin that ignores S105/S106 in its own pyproject loses that to
    `make update-template`, so the rule lives in the template, which sync_template.py derives from ours."""
    core = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    ignored = core["tool"]["ruff"]["lint"]["per-file-ignores"]["**/tests/**/*.py"]
    template = (TEMPLATE / "pyproject.toml.jinja").read_text(encoding="utf-8")
    block = re.search(r'^"\*\*/tests/\*\*/\*\.py" = \[(.*?)^\]', template, re.MULTILINE | re.DOTALL)
    assert block
    assert {"S105", "S106"} <= set(ignored)
    assert re.findall(r'^\s*"([A-Z]+\d*)"', block[1], re.MULTILINE) == ignored


# Environment variables: tests and the template use only HZ_* names that something reads.

ENV_NAME = re.compile(r"(?<![A-Za-z0-9_])HZ_[A-Z0-9_]+")
ENV_READ = re.compile(r"""(?:environ\.get\(|environ\[|getenv\()\s*["'](HZ_[A-Z0-9_]+)|\$\{?(HZ_[A-Z0-9_]+)""")
CODE_SUFFIXES = (".py", ".sh", ".jinja")
NOT_SETTINGS = {  # names or prefixes that aren't settings of Heinzelmännchen's modules: why they are fine
    "HZ_URL": "a constant in tests/e2e/docs_test.py, not a variable",
    "HZ_CREDENTIAL_": "credentials in the environment fallback, named per declared credential (ADR 0030)",
    "HZ_EXAMPLE__": "settings of the `example` module tests define",
    "HZ_WORKFLOW__": "the heinzel module's old prefix, read for one minor release (ADR 0051)",
    "HZ_A_B__": "the variable prefix of a module named `a-b`, in a test of env_prefix",
    "HZ_SYSTEM_CHANGELOG__": "the variable prefix of a nested module, in a test of env_prefix",
}


def env_names(text: str) -> set[str]:
    """Every HZ_* name in a text."""
    return set(ENV_NAME.findall(text))


def env_reads(text: str) -> set[str]:
    """HZ_* variables a Python or shell text reads: os.environ / os.getenv with a literal name, or $NAME."""
    return {python or shell for python, shell in ENV_READ.findall(text)}


def test_finds_env_names_and_reads():
    hz = "HZ" + "_"  # split, so this file uses no names nothing reads
    text = f'setenv("{hz}X", x); os.environ.get("{hz}A"); os.getenv("{hz}B"); echo "${{{hz}C:-x}} ${hz}D"; NOT_{hz}E'
    assert env_names(text) == {f"{hz}X", f"{hz}A", f"{hz}B", f"{hz}C", f"{hz}D"}
    assert env_reads(text) == {f"{hz}A", f"{hz}B", f"{hz}C", f"{hz}D"}


def test_tests_and_template_use_only_variables_something_reads(make_kernel):
    """A variable nothing reads isolates nothing: settings (`HZ_<FIELD>`, `HZ_<MODULE>__<FIELD>`) and the variables
    code reads by name (HZ_CONFIG, HZ_CONFIG_HOME, HZ_SOURCE in install.sh, HZ_SPEC in the template's e2e test)."""
    kernel = make_kernel(
        builtins=True,
    )
    known = {
        f"{env_prefix(section)}{key.upper()}"
        for section, settings in kernel.settings_of()
        for key in type(settings).model_fields
    }
    code = [path for path in repository_files() if path.endswith(CODE_SUFFIXES)]
    known |= {name for path in code for name in env_reads((REPO / path).read_text(encoding="utf-8"))}
    used = {
        (name, path)
        for path in repository_files()
        if path.startswith(("tests/", "template/"))
        for name in env_names((REPO / path).read_text(encoding="utf-8", errors="ignore"))
    }
    unknown = sorted(
        f"{path}: {name}" for name, path in used if name not in known and not name.startswith(tuple(NOT_SETTINGS))
    )
    assert unknown == [], "nothing reads these; use a setting's variable (see `hz system config show`)"


# Claude Code hooks: CLAUDE.md names what the Bash guard blocks, the same way for Heinzelmännchen and plugins.

GUARD_RULES = _load(REPO / ".claude" / "hooks" / "guard_bash.py").RULES
BLOCKED = re.compile(r"\bblock (.+?), and refuse", re.DOTALL)


def blocked_in(claude_md: str) -> list[str]:
    """What the hooks sentence of a CLAUDE.md says the Bash guard blocks, one entry per rule."""
    clause = BLOCKED.search(claude_md)
    assert clause, "CLAUDE.md lost its hooks sentence (`... block A, B and C, and refuse ...`)"
    return re.split(r",\s+|\s+and\s+", " ".join(clause[1].split()))


def test_finds_what_claude_md_says_is_blocked():
    text = "Hooks lint, block `--no-verify`, `--force` and pull requests\nnot targeting `main`, and refuse to end"
    assert blocked_in(text) == ["`--no-verify`", "`--force`", "pull requests not targeting `main`"]


@pytest.mark.parametrize("root", [REPO, TEMPLATE], ids=["heinzel", "template"])
def test_claude_md_names_every_bash_guard_rule(root):
    """Agents learn from CLAUDE.md what the guard stops; a rule added to guard_bash.py is named in both files."""
    blocked = blocked_in((root / "CLAUDE.md").read_text(encoding="utf-8"))
    assert len(blocked) == len(GUARD_RULES), f"name every rule of .claude/hooks/guard_bash.py: {blocked}"
    assert blocked == blocked_in((REPO / "CLAUDE.md").read_text(encoding="utf-8"))


# Dependencies: every declaration says which version it needs at least.

LOWER_BOUNDS = {">=", ">", "==", "~=", "==="}
PYPROJECTS = [
    REPO / "pyproject.toml",
    TEMPLATE / "pyproject.toml.jinja",
]


def _requirements(text: str) -> list[str]:
    text = re.sub(r'\[\[ "([^"]*)" \]\]', r"\1", text)  # the template's escaped [[ and [#
    data = tomllib.loads(re.sub(r"\[\[ \w+ \]\]", "1.0", text))  # its variables
    project = data.get("project", {})
    return [
        *project.get("dependencies", []),
        *(r for extra in project.get("optional-dependencies", {}).values() for r in extra),
        *(r for group in data.get("dependency-groups", {}).values() for r in group if isinstance(r, str)),
        *data.get("build-system", {}).get("requires", []),
    ]


def unbounded(requirements: list[str]) -> list[str]:
    """Requirements without a lower bound; a direct URL pins one."""
    parsed = [Requirement(r) for r in requirements]
    return [str(r) for r in parsed if not r.url and not {s.operator for s in r.specifier} & LOWER_BOUNDS]


def test_finds_unbounded_requirements():
    assert unbounded(["a", "b>=1", "c<2", "d==1", "e @ git+https://example.com/e", "f~=1.2"]) == ["a", "c<2"]


@pytest.mark.parametrize("pyproject", PYPROJECTS, ids=lambda p: str(p.relative_to(REPO)))
def test_every_dependency_has_a_lower_bound(pyproject):
    """An unbounded dependency resolves to anything, including versions the code was never tested with."""
    assert unbounded(_requirements(pyproject.read_text(encoding="utf-8"))) == []


# Modules return results and heads present them: a module writing to the terminal breaks MCP and REST.

MODULE_CODE = [*REPO.glob("src/heinzel/modules/**/*.py")]


def terminal_writes(source: str) -> list[int]:
    """Lines that use `rich.console.Console`, `sys.stdout` or `sys.stderr`."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        match node:
            case ast.ImportFrom(module="rich.console", names=names) if any(a.name == "Console" for a in names):
                lines.append(node.lineno)
            case ast.ImportFrom(module="sys", names=names) if any(a.name in {"stdout", "stderr"} for a in names):
                lines.append(node.lineno)
            case ast.Attribute(value=ast.Name(id="sys"), attr="stdout" | "stderr"):
                lines.append(node.lineno)
            case ast.Attribute(value=ast.Attribute(value=ast.Name(id="rich"), attr="console"), attr="Console"):
                lines.append(node.lineno)
            case _:
                pass
    return sorted(lines)


def test_finds_terminal_writes():
    source = (
        "import sys\nimport rich.console\nfrom rich.console import Console, RenderableType\n"
        "sys.stdout.write('x')\nrich.console.Console()\nfrom sys import stderr\nsys.version_info\n"
    )
    assert terminal_writes(source) == [3, 4, 5, 6]


@pytest.mark.parametrize("path", MODULE_CODE, ids=lambda p: str(p.relative_to(REPO)))
def test_modules_never_write_to_the_terminal(path):
    assert terminal_writes(path.read_text(encoding="utf-8")) == [], "return a result; the head presents it"


# Operations: injected parameters (kernel, settings, services) first, then what the caller passes.


def injected_after_exposed(kernel: Kernel) -> list[str]:
    """Operations where a parameter the kernel injects follows one a head exposes."""
    found = []
    for record, operation in kernel.lets(OperationLet):
        parameters = operation.signature().parameters.values()
        injected = [kernel.provides(get_origin(p.annotation) or p.annotation) for p in parameters]
        if injected != sorted(injected, reverse=True):
            found.append(f"{record.qualname} {operation.name}")
    return found


def test_finds_injected_parameters_after_exposed_ones(make_kernel):
    module = Module("example", "An example.")

    @module.operation
    def greet(name: str, kernel: Kernel) -> str:
        """Greet someone."""
        return f"{name} {kernel}"

    assert injected_after_exposed(make_kernel(example=module)) == ["example greet"]


def test_injected_parameters_come_first(make_kernel):
    """Reading a signature, the dependencies come first and the interface follows, in every module and plugin."""
    assert (
        injected_after_exposed(
            make_kernel(
                builtins=True,
            )
        )
        == []
    )


# Makefiles: every target is documented in `make help` and phony; Markdown names only targets that exist.

MAKEFILES = {REPO / "Makefile": REPO, TEMPLATE / "Makefile": TEMPLATE}
TARGET = re.compile(r"^([a-zA-Z0-9_-]+):(?!=)(.*)$", re.MULTILINE)
MAKE_IN_MARKDOWN = re.compile(r"`make ([a-z][a-z0-9-]*)")
HISTORY = ("docs/adr/", "docs/research/", "CHANGELOG.md")  # records of their time, not instructions


def makefile_problems(text: str) -> list[str]:
    """Targets without a `## help` comment or missing from .PHONY."""
    phony = {name for line in re.findall(r"^\.PHONY:(.*)$", text, re.MULTILINE) for name in line.split()}
    targets = TARGET.findall(text)
    problems = [f"{target}: no ## help" for target, rest in targets if "## " not in rest]
    return problems + [f"{target}: not .PHONY" for target, _ in targets if target not in phony]


def test_finds_makefile_problems():
    text = ".PHONY: a b\na: ## A\n\techo\nb:\n\techo\nc: ## C\n\techo\nX := 1\n"
    assert makefile_problems(text) == ["b: no ## help", "c: not .PHONY"]


@pytest.mark.parametrize("makefile", MAKEFILES, ids=lambda p: str(p.relative_to(REPO)))
def test_every_make_target_is_documented_and_phony(makefile):
    assert makefile_problems(makefile.read_text(encoding="utf-8")) == []


def _markdown(root: Path) -> list[Path]:
    """The Markdown that root's Makefile serves.

    The template's own files for the template; its shared, symlinked ones describe Heinzelmännchen. Everything else
    for Heinzelmännchen, except history (ADRs, research, changelog).
    """
    files = [REPO / p for p in repository_files() if p.endswith((".md", ".md.jinja"))]
    if root == TEMPLATE:
        return [p for p in files if p.is_relative_to(TEMPLATE) and not p.is_symlink()]
    return [
        p for p in files if not p.is_relative_to(TEMPLATE) and not p.relative_to(REPO).as_posix().startswith(HISTORY)
    ]


@pytest.mark.parametrize("makefile", MAKEFILES, ids=lambda p: str(p.relative_to(REPO)))
def test_markdown_names_only_make_targets_that_exist(makefile):
    """Heinzelmännchen's docs may name a plugin's targets (`make update-template`); the template only its own."""
    targets = {target for target, _ in TARGET.findall(makefile.read_text(encoding="utf-8"))}
    if makefile == REPO / "Makefile":
        targets |= {target for target, _ in TARGET.findall((TEMPLATE / "Makefile").read_text(encoding="utf-8"))}
        starter = next((REPO / "starter").glob("*Makefile*.jinja")).read_text(encoding="utf-8")
        targets |= {
            name
            for group in re.findall(r"(?m)^([a-z][\w-]*(?:[ \t]+[a-z][\w-]*)*):(?!=)", starter)
            for name in group.split()
        }
    missing = [
        f"{p.relative_to(REPO)}: make {target}"
        for p in _markdown(MAKEFILES[makefile])
        for target in MAKE_IN_MARKDOWN.findall(p.read_text(encoding="utf-8"))
        if target not in targets
    ]
    assert missing == []


def test_finds_make_targets_in_markdown():
    assert MAKE_IN_MARKDOWN.findall("run `make check`, `make pr TITLE=x`; make sense of `uv run`") == ["check", "pr"]


def lock_policy_valid(configuration):
    """A generated lock exception needs a required resolver check; ordinary file limits stay unchanged."""
    hooks = {hook["id"]: hook for repo in configuration["repos"] for hook in repo["hooks"]}
    large, lock = hooks["check-added-large-files"], hooks.get("uv-lock", {})
    return (
        large.get("exclude") == r"^uv\.lock$"
        and not large.get("args")
        and lock.get("entry") == "uv lock --check"
        and lock.get("files") == r"^(pyproject\.toml|uv\.lock)$"
        and lock.get("pass_filenames") is False
        and lock.get("language") == "system"
    )


def test_large_generated_locks_require_resolver_validation_without_relaxing_other_file_limits():
    configuration = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text())
    assert lock_policy_valid(configuration)
    validator = next(repo for repo in configuration["repos"] if repo["repo"] == "local")["hooks"]
    validator[:] = [hook for hook in validator if hook["id"] != "uv-lock"]
    assert not lock_policy_valid(configuration)
    configuration = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text())
    configuration["repos"][0]["hooks"][0]["exclude"] = ".*"
    assert not lock_policy_valid(configuration)


def matching_toolkit_pins(pins):
    """Every authoring entry point must use the same final wheel version and exact digest."""
    pattern = (
        r"https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/"
        r"tools-v(?P<version>\d+\.\d+\.\d+)/heinzel_plugin_tools-(?P=version)-py3-none-any.whl"
        r"#sha256=[a-f0-9]{64}"
    )
    return bool(pins) and len(set(pins)) == 1 and all(re.fullmatch(pattern, pin) for pin in pins)


def test_standalone_commands_workflows_and_journey_pin_one_immutable_toolkit():
    starter = REPO / "starter"
    paths = [
        next(starter.glob("*Makefile*.jinja")),
        *starter.glob("*github*/workflows/*.yml.jinja"),
        REPO / ".github/workflows/release.yml",
    ]
    pins = [_load(REPO / "scripts/starter-test.py").TOOLKIT.rsplit(" ", 1)[-1]]
    pins.append(_load(REPO / "scripts/scoped_checks.py").TOOLKIT.rsplit(" ", 1)[-1])
    for path in paths:
        text = path.read_text().replace(r"\#", "#")
        match = re.search(r"https://github\.com/\S+?\.whl(?:#sha256=[a-f0-9]{64})?", text)
        assert match, path
        pins.append(match.group())
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text())
    hook = next(hook for repo in config["repos"] for hook in repo["hooks"] if hook["id"] == "owning-plugin-checks")
    pins.append(hook["args"][0].rsplit(" ", 1)[-1])
    assert len(pins) == 7
    assert matching_toolkit_pins(pins)
    bootstrap = _load(REPO / "scripts/plugin_tooling.py")
    assert bootstrap.TOOLKIT.rsplit(" ", 1)[-1] == pins[0]
    requirements = (REPO / "scripts/plugin-tooling-requirements.txt").read_text().splitlines()
    entries = [line for line in requirements if line and not line.startswith("#")]
    assert len(entries) == 3
    toolkit_url, toolkit_digest = pins[0].split("#sha256=")
    assert entries[0] == f"heinzel-plugin-tools @ {toolkit_url} --hash=sha256:{toolkit_digest}"
    lock = tomllib.loads((REPO / "uv.lock").read_text())
    packages = {package["name"]: package for package in lock["package"]}
    assert {entry.split("==", 1)[0] for entry in entries[1:]} == {"packaging", "typos"}
    for entry in entries[1:]:
        requirement, *hashes = entry.split()
        assert re.fullmatch(r"(?:packaging|typos)==[0-9.]+", requirement)
        assert hashes
        assert all(re.fullmatch(r"--hash=sha256:[a-f0-9]{64}", value) for value in hashes)
        name, version = requirement.split("==")
        assert packages[name]["version"] == version
        assert {value.removeprefix("--hash=") for value in hashes} <= {
            wheel["hash"] for wheel in packages[name]["wheels"]
        }


def test_plugin_only_pushes_and_commits_use_owning_checks_instead_of_core_checks():
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text())
    hooks = {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}
    path = "plugins/heinzel-unforeseen/src/feature.py"
    for name in ("check-fast", "mypy", "ruff-format", "ruff-check"):
        assert not hooks[name].get("always_run")
        assert re.search(hooks[name]["exclude"], path)
        assert not re.search(hooks[name]["exclude"], "src/heinzel/kernel/_kernel.py")
    owner = hooks["owning-plugin-checks"]
    assert owner["pass_filenames"] is False
    assert owner["always_run"] is True
    assert owner["args"][-2:] == ["check-changed", "--git-hook"]
    assert owner["require_serial"] is True
    assert set(owner["stages"]) == {"pre-commit", "pre-push"}


def test_core_can_release_an_unknown_source_plugin_without_core_build_or_tests():
    workflow = yaml.safe_load((REPO / ".github/workflows/release.yml").read_text())
    build = workflow["jobs"]["build"]
    commands = "\n".join(step.get("run", "") for step in build["steps"])
    assert 'hz-plugin verify-release "$PLUGIN"' in commands
    assert "make check" not in commands
    assert "make build" not in commands
    assert "pytest" not in commands
    assert workflow["jobs"]["publish"]["environment"] == "plugin-pypi"
    assert 'test "$PUBLISH" != true || test "$REF" = refs/heads/main' in commands


def test_unhashed_or_drifting_toolkit_pins_are_refused():
    base = (
        "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/"
        "tools-v0.1.1/heinzel_plugin_tools-0.1.1-py3-none-any.whl"
    )
    pinned = base + "#sha256=" + "a" * 64
    assert matching_toolkit_pins([pinned, pinned])
    assert not matching_toolkit_pins([base, base])
    assert not matching_toolkit_pins([pinned, pinned.replace("a" * 64, "b" * 64)])


# The working title is gone; only the ADR that chose the name and the changelog remember it.

OLD_NAME = re.compile("(?<![a-z])" + "j" + "oy" + "(?![a-z])", re.IGNORECASE)  # split, so this file doesn't match
OLD_NAME_ALLOWED = (
    "docs/adr/0016-",
    "docs/adr/README.md",  # its log of ADR edits names a hook renamed away from the working title
    "CHANGELOG.md",
)


def test_finds_the_old_name():
    text = "test_" + "j" + "oy_errors, J" + "OY, " + "j" + "oyful, enj" + "oy"
    assert OLD_NAME.findall(text) == ["j" + "oy", "J" + "OY"]


BINARY = (".gif", ".webp", ".mp4", ".png", ".woff2", ".jpg", ".ico")
"""Recordings and images: their bytes aren't words, and a GIF holds any three letters somewhere."""


def test_the_working_title_is_gone():
    found = [
        path
        for path in repository_files()
        if not path.startswith(OLD_NAME_ALLOWED)
        and (
            OLD_NAME.search(path)
            or (
                not path.endswith(BINARY)
                and OLD_NAME.search((REPO / path).read_text(encoding="utf-8", errors="ignore"))
            )
        )
    ]
    assert found == [], "say Heinzelmännchen, hz or heinzel"


def test_plugins_start_at_the_version_their_release_process_knows():
    """bump-my-version's current_version must equal the [project] version, in the core and in the template."""
    for path in (REPO / "pyproject.toml", REPO / "template" / "pyproject.toml.jinja"):
        text = path.read_text(encoding="utf-8")
        project = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
        bump = re.search(r'^current_version = "([^"]+)"', text, re.MULTILINE)
        assert project, path.name
        assert bump, path.name
        assert project.group(1) == bump.group(1), path.name
