"""CI runs what developers run locally."""

import re
import tomllib
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]

WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))
# Steps that legitimately don't go through make, by workflow: setup, checks that only exist in CI, and release
# plumbing. Everything else is a make target, so it runs the same way locally.
NOT_MAKE = {  # workflow: {start of the step's command: why}
    "*": {"uv sync": "installs what the job needs"},
    "ci.yml": {
        "uv run python scripts/check_confidentiality.py --require": "the secret must be configured in this repository",
        "curl ": "downloads the pinned gitleaks binary",
        "./gitleaks ": "scans the whole history; locally the pre-commit hook scans each commit",
        "unzip -l ": "asserts what the built wheel contains",
        "for result in $RESULTS": "the CI OK gate aggregates the results of the other jobs",
    },
    "demo.yml": {"uv tool install --editable .": "installs hz from this checkout for the recording"},
    "publish.yml": {
        'test "v$(uv version --short)" = "$TAG"': "refuses a tag that doesn't match the project version",
        "uv run python scripts/check_confidentiality.py": "scans the tagged tree with the term list from the secret",
        'name="$(uv version': "exports the release's CycloneDX bill of materials",
        "uv run --frozen python scripts/release_notes.py": "writes the release notes",
        'cp "$BUNDLE"': "attaches the provenance attestation and creates the GitHub release",
    },
    "release.yml": {
        "git config user.name": "opens the release pull request as the bot and hands its tag to the next step",
        "gh workflow run ci.yml": "starts CI on the release pull request, which the bot's token doesn't",
    },
    "release-tag.yml": {'tag="v$(uv version --short)"': "tags a merged release commit"},
    "claude-review.yml": {'files="$(git diff': "picks the review tier from the changed files"},
    "update-prs.yml": {
        "gh pr list": "updates auto-merging pull requests with main",
        "threads='query(": "resolves the review threads of closed code-scanning alerts on auto-merging pull requests",
    },
}


def commands(workflow: Path) -> list[str]:
    """The `run:` commands of a workflow's steps, without leading comment lines (such as shellcheck directives)."""
    spec = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    return [
        _without_leading_comments(step["run"])
        for job in spec["jobs"].values()
        for step in job.get("steps", [])
        if "run" in step
    ]


def _without_leading_comments(script: str) -> str:
    lines = script.strip().splitlines()
    while lines and lines[0].lstrip().startswith("#"):
        lines.pop(0)
    return "\n".join(lines).strip()


def not_through_make(workflow: Path) -> list[str]:
    """Commands that should be a make target: neither `make ...` nor exempt for this workflow."""
    exempt = (*NOT_MAKE["*"], *NOT_MAKE.get(workflow.name, {}))
    return [run for run in commands(workflow) if not run.startswith(("make ", *exempt))]


def test_finds_commands_not_run_through_make(tmp_path):
    workflow = tmp_path / "publish.yml"
    steps = [{"run": "make build"}, {"run": "uv sync"}, {"run": "bash scripts/check_licenses.sh"}]
    workflow.write_text(yaml.safe_dump({"jobs": {"publish": {"steps": [{"uses": "x"}, *steps]}}}), encoding="utf-8")
    assert not_through_make(workflow) == ["bash scripts/check_licenses.sh"]


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_workflows_run_checks_through_make(workflow):
    """Locally and in CI the same make targets run, so a green `make check` means a green CI and a good release."""
    assert not_through_make(workflow) == [], "run these through a make target, or add them to NOT_MAKE with a reason"


def test_every_not_make_exemption_is_used():
    runs = {workflow.name: commands(workflow) for workflow in WORKFLOWS}
    runs["*"] = [run for each in runs.values() for run in each]
    stale = [
        f"{name}: {start}"
        for name, exempt in NOT_MAKE.items()
        for start in exempt
        if not any(run.startswith(start) for run in runs.get(name, []))
    ]
    assert stale == [], "remove stale NOT_MAKE entries"


COMMITS = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["commitizen"]


def conventional(message: str) -> bool:
    """What `cz check` decides (commit-msg hook, `make commits`, CI): an allowed prefix, or the schema_pattern."""
    allowed = message.startswith(tuple(COMMITS["allowed_prefixes"]))
    return allowed or re.match(COMMITS["customize"]["schema_pattern"], message) is not None


@pytest.mark.parametrize(
    "message",
    [
        "feat(cli): add --json to every operation",
        "fix: exit with code 1 on errors",
        "feat(security)!: refuse plugins from unknown hosts",
        "chore(release): v0.2.0",
        "docs(adr): record the public API (#41)",
        "build: bump the dependencies group across 1 directory with 12 updates (#77)",  # Dependabot
        "Merge branch 'main' into feat/x",
        "fix(cli): " + "a" * 62,  # 72 characters
        "feat(kernel): " + "a" * 58 + " (#123)",  # 72 plus what the squash merge appends
        "refactor(heads): share the exposure rules\n\nThe MCP and REST heads had drifted.\n\nRefs: #7",
    ],
)
def test_commit_grammar_accepts(message):
    assert conventional(message)


@pytest.mark.parametrize(
    "message",
    [
        "feat(standards): not a scope",
        "feature: not a type",
        "fix(cli):no space",
        "fix(cli): ",
        "Fix(cli): capitalised type",
        "fix(cli): " + "a" * 63,  # 73 characters
        "feat(kernel): " + "a" * 59 + " (#123)",
        "fix: summary\nbody without a blank line",
    ],
)
def test_commit_grammar_rejects(message):
    assert not conventional(message)


def test_documented_commit_scopes_are_the_enforced_ones():
    standards = (REPO / "docs" / "CODING_STANDARDS.md").read_text(encoding="utf-8")
    documented = re.search(r"Heinzelmännchen's are (.*?)\. A plugin", standards, re.DOTALL)
    enforced = re.search(r"\\\(\(([a-z|-]+)\)\\\)", COMMITS["customize"]["schema_pattern"])
    assert documented
    assert enforced
    assert re.findall(r"`([a-z-]+)`", documented[1]) == enforced[1].split("|")


def jobs_without_timeout(workflow: dict) -> list[str]:
    """Jobs that could run for GitHub's default six hours; a job calling a reusable workflow inherits its timeouts."""
    return [
        name for name, job in workflow.get("jobs", {}).items() if "uses" not in job and "timeout-minutes" not in job
    ]


def test_finds_jobs_without_timeout():
    workflow = {"jobs": {"a": {"runs-on": "x"}, "b": {"runs-on": "x", "timeout-minutes": 5}, "c": {"uses": "./w.yml"}}}
    assert jobs_without_timeout(workflow) == ["a"]


@pytest.mark.parametrize("workflow", sorted((REPO / ".github" / "workflows").glob("*.yml")), ids=lambda p: p.name)
def test_every_job_has_a_timeout(workflow):
    """A hung job fails fast instead of holding a runner for six hours."""
    assert jobs_without_timeout(yaml.safe_load(workflow.read_text(encoding="utf-8"))) == [], "add timeout-minutes"


def handlers_of_review_threads(root: Path) -> list[str]:
    """Top-level directories of the workflows and scripts that read or resolve review threads (`reviewThreads`)."""
    candidates = [*(root / ".github" / "workflows").glob("*.yml"), *(root / "scripts").glob("*.py")]
    return sorted(
        {p.relative_to(root).parts[0] for p in candidates if "reviewThreads" in p.read_text(encoding="utf-8")}
    )


def test_finds_handlers_of_review_threads(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".github" / "workflows" / "a.yml").write_text("run: gh pr list", encoding="utf-8")
    assert handlers_of_review_threads(tmp_path) == []
    (tmp_path / "scripts" / "b.py").write_text("QUERY = 'reviewThreads(first: 100)'", encoding="utf-8")
    assert handlers_of_review_threads(tmp_path) == ["scripts"]


def test_required_review_threads_are_surfaced_and_resolved():
    """main requires resolved review threads, and an unresolved bot thread blocks auto-merge without a word. So a
    workflow resolves the threads of fixed or dismissed code-scanning alerts, and `make ci-logs` names the rest."""
    if "resolved review threads" not in (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8"):
        pytest.skip("main does not require resolved review threads")
    assert handlers_of_review_threads(REPO) == [".github", "scripts"]


def test_codeql_runs_security_queries_only():
    """Ruff, mypy and pyright own code quality; CodeQL's quality queries contradict them (a quoted cast, overload
    stubs, PEP 695 type parameters), and tools that disagree are a configuration bug."""
    spec = yaml.safe_load((REPO / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8"))
    inits = [
        step.get("with", {})
        for job in spec["jobs"].values()
        for step in job["steps"]
        if step.get("uses", "").startswith("github/codeql-action/init@")
    ]
    assert inits
    assert all(init.get("queries", "") in {"", "security-extended"} for init in inits), "use security-extended"


def test_the_release_regenerates_derived_files():
    """A version bump changes files that other files are generated from; the release commit must include both."""
    script = (REPO / "scripts" / "release.sh").read_text(encoding="utf-8")
    bump, fix, commit = (
        script.index(marker) for marker in ("bump-my-version bump", "make --no-print-directory fix", "git commit")
    )
    assert bump < fix < commit


def test_pushing_runs_check_fast_through_a_hook():
    """What CI would catch first (format, lint, contracts, generated files, unit tests) fails before the push."""
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}
    assert "exec make check-fast" in hooks["check-fast"]["entry"]
    assert hooks["check-fast"]["stages"] == ["pre-push"]
    assert "pre-push" in config["default_install_hook_types"]
    assert "--hook-type pre-push" in (REPO / "Makefile").read_text(encoding="utf-8")
    assert (REPO / "template" / ".pre-commit-config.yaml").resolve() == REPO / ".pre-commit-config.yaml"


def test_the_template_audit_runs_in_ci_and_in_full_preflight():
    """pip-audit needs network, so the rendered plugin's audit is not part of the offline `make check`."""
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "\tTEMPLATE_AUDIT=$(AUDIT) bash scripts/template-test.sh" in makefile
    assert "TEMPLATE_AUDIT=$(if $(filter full,$(PREFLIGHT)),1) bash scripts/template-test.sh" in makefile
    assert "make audit" in (REPO / "scripts" / "template-test.sh").read_text(encoding="utf-8")
    ci = "\n".join(commands(REPO / ".github" / "workflows" / "ci.yml"))
    assert "make template-test AUDIT=1" in ci
