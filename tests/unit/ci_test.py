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
        "uv run --script scripts/check_confidentiality.py --require": "the secret must be configured in this repo",
        "unzip -l ": "asserts what the built wheel contains",
        'changed="$(git diff --name-only': "decides from the changed paths whether a pull request builds the macOS app",
        "for result in $RESULTS": "the CI OK gate aggregates the results of the other jobs",
        "tar -czf notifier.tar.gz": "packs the app the macOS job built, with its executable bit, for the build job",
        "tar -xzf notifier.tar.gz": "unpacks Heinzel Notifier into the package before the wheel is built",
    },
    "docs.yml": {
        "sudo apt-get update": "fonts for the terminal recordings, from the runner's packages",
        "uv run --quiet --group docs playwright install-deps": "Chromium's system libraries, from the runner",
    },
    "publish.yml": {
        'test "v$(uv version --short)" = "$TAG"': "refuses a tag that doesn't match the project version",
        "uv run --script scripts/check_confidentiality.py": "scans the tagged tree with the term list from the secret",
        'name="$(uv version': "exports the release's CycloneDX bill of materials",
        "uv run --script scripts/release_notes.py": "writes the release notes",
        'cp "$BUNDLE"': "attaches the provenance attestation and creates the GitHub release",
        "tar -czf notifier.tar.gz": "packs the app the macOS job built, with its executable bit, for the build job",
        "tar -xzf notifier.tar.gz": "unpacks Heinzel Notifier into the package before the wheel is built",
        "unzip -l ": "asserts that the released wheel ships Heinzel Notifier",
    },
    "pypi.yml": {
        '[[ "$TAG" =~': "binds publishing to the exact tag and main ancestry before build tooling",
        "gh api ": "verifies the published GitHub release and exports its exact source commit",
        "uv run --script scripts/check_confidentiality.py --require": "requires the configured release term list",
        "tar -czf notifier.tar.gz": "preserves the notifier executable bits and signature across runners",
        "tar -xzf notifier.tar.gz": "restores the notifier from the same validated tag",
        "uv build --package heinzel": "builds only the registered core project, never unpublished workspace plugins",
        "uv run --script scripts/pypi_release.py artifacts": "checks core metadata and complete packaged payloads",
        "python3 - <<'PY'": "rechecks hashes without checking out or running project code in the OIDC job",
    },
    "prepare-release.yml": {
        'if [[ ! "$INTRO"': "rejects blank dispatch input before checkout or release work",
        "git config user.name": "opens the release pull request as the bot and hands its tag to the next step",
        "gh workflow run ci.yml": "starts CI on the release pull request, which the bot's token doesn't",
        "gh pr merge": "enables squash auto-merge on the release pull request once CI OK is green",
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


def test_managed_backend_verification_gates_ci_without_leaking_into_plugin_templates():
    """The source-only native owner is checked by required CI; plugins never install its private Node toolchain."""
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    assert "lint" in jobs["ci-ok"]["needs"]
    assert any(step.get("run") == "make managed-backend-setup managed-backend-check" for step in jobs["lint"]["steps"])
    runs = [step.get("run") for step in jobs["lint"]["steps"]]
    assert runs.index("make managed-backend-setup managed-backend-check") < runs.index("make typecheck")
    for name in ("Makefile", ".github/workflows/ci.yml.jinja"):
        assert "managed-backend" not in (REPO / "template" / name).read_text()


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


def test_release_docs_only_publish_existing_recordings_even_when_the_caller_was_a_push():
    release = yaml.safe_load((REPO / ".github/workflows/release-tag.yml").read_text(encoding="utf-8"))
    docs = yaml.safe_load((REPO / ".github/workflows/docs.yml").read_text(encoding="utf-8"))
    assert release["jobs"]["docs"].get("with", {}).get("site_only") is True
    assert docs[True]["workflow_call"]["inputs"]["site_only"]["default"] is False
    assert docs["jobs"]["record"]["if"] == (
        "inputs.site_only != true && (github.event_name == 'push' || inputs.record == true)"
    )


# A script named by path needs its executable bit, which copier drops when it follows the template's symlinks into a
# plugin's scripts/. Through an interpreter, a script runs without it.
SCRIPT = re.compile(r"(?<![\w/.-])(?:\./)?scripts/[\w.-]+\.(?:sh|py)\b")
INTERPRETER = re.compile(r"(?:\bbash|\bpython3?|\buv run(?: -[\w-]+)*(?: --script| python3?))\s+$")


def scripts_run_by_path(run: str) -> list[str]:
    """The scripts a `run:` command executes by path instead of through `bash` or `python`."""
    found = []
    for line in run.splitlines():
        code = line.split(" #", 1)[0]
        found += [m.group() for m in SCRIPT.finditer(code) if not INTERPRETER.search(code[: m.start()])]
    return found


def test_finds_scripts_run_by_path():
    assert scripts_run_by_path('tag="$(scripts/release.sh "$BUMP" | tail -n 1)"') == ["scripts/release.sh"]
    assert scripts_run_by_path("./scripts/x.py --check") == ["./scripts/x.py"]
    assert scripts_run_by_path("sh scripts/x.sh") == ["scripts/x.sh"]
    assert scripts_run_by_path("bash scripts/x.sh && python scripts/y.py") == []
    assert scripts_run_by_path("uv run --frozen python scripts/x.py > notes.md") == []
    assert scripts_run_by_path("uv run --quiet --script scripts/x.py --json") == []
    assert scripts_run_by_path("make check  # see scripts/steps.py") == []


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_workflows_run_scripts_through_an_interpreter(workflow):
    """Plugins get these workflows (symlinked, or ci.yml generated by sync_template.py) and their scripts without the
    executable bit, so `scripts/release.sh` fails there with Permission denied; `bash scripts/release.sh` works."""
    by_path = [script for run in commands(workflow) for script in scripts_run_by_path(run)]
    assert by_path == [], "run scripts as `bash scripts/x.sh` or `uv run --script scripts/x.py` (scripts_test.py)"


def test_make_runs_scripts_through_an_interpreter():
    """`make pr` and `make release` are how people run pr.sh and release.sh, in plugins too."""
    recipes = [line for line in (REPO / "Makefile").read_text(encoding="utf-8").splitlines() if line.startswith("\t")]
    assert [script for line in recipes for script in scripts_run_by_path(line)] == []


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


def test_the_release_keeps_changelog_introductions():
    """git-cliff regenerates CHANGELOG.md from commits; the prose written under release headings is put back, the
    new release's from INTRO (the Release workflow's `intro` input), and the pull request shows the merged section."""
    script = (REPO / "scripts" / "release.sh").read_text(encoding="utf-8")
    save, regenerate, restore, commit, notes = (
        script.index(marker)
        for marker in (
            'cp CHANGELOG.md "$work/previous.md"',
            "git-cliff --offline --tag",
            'uv run --script scripts/changelog_intros.py "$work/previous.md" CHANGELOG.md --version "$next"',
            "git commit",
            'uv run --script scripts/release_notes.py "$tag"',
        )
    )
    assert save < regenerate < restore < commit < notes
    assert '--intro "${INTRO:-}"' in script
    release = yaml.safe_load((REPO / ".github" / "workflows" / "prepare-release.yml").read_text(encoding="utf-8"))
    assert "intro" in release[True]["workflow_dispatch"]["inputs"]  # YAML 1.1 reads the key `on` as True
    step = next(s for s in release["jobs"]["pull-request"]["steps"] if "release.sh" in s.get("run", ""))
    assert any(
        "gh pr merge" in s.get("run", "") and "--auto" in s["run"] for s in release["jobs"]["pull-request"]["steps"]
    )
    assert step["env"]["INTRO"] == "${{ inputs.intro }}"
    assert (REPO / "template" / "scripts" / "changelog_intros.py").resolve() == REPO / "scripts" / "changelog_intros.py"


def test_pushing_runs_check_fast_through_a_hook():
    """What CI would catch first (format, lint, contracts, generated files, unit tests) fails before the push."""
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}
    assert "exec make check-fast" in hooks["check-fast"]["entry"]
    assert hooks["check-fast"]["stages"] == ["pre-push"]
    assert "pre-push" in config["default_install_hook_types"]
    assert "--hook-type pre-push" in (REPO / "Makefile").read_text(encoding="utf-8")
    assert (REPO / "template" / ".pre-commit-config.yaml").resolve() == REPO / ".pre-commit-config.yaml"


def test_the_template_audit_runs_in_template_test_ci_and_full_preflight():
    """The rendered plugin's audit takes seconds, so `make template-test` runs it unless `AUDIT=0` (offline).

    `make preflight` stays offline unless `PREFLIGHT=full`, like the audit of Heinzelmännchen's own dependencies.
    """
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "\tTEMPLATE_AUDIT=$(if $(filter 0,$(AUDIT)),,1) bash scripts/template-test.sh" in makefile
    assert "template-test" not in (REPO / "template" / "Makefile").read_text(encoding="utf-8")
    assert "TEMPLATE_AUDIT=$(if $(filter full,$(PREFLIGHT)),1) bash scripts/template-test.sh" in makefile
    assert "make audit" in (REPO / "scripts" / "template-test.sh").read_text(encoding="utf-8")
    ci = "\n".join(commands(REPO / ".github" / "workflows" / "ci.yml"))
    assert "make template-test AUDIT=1" in ci


def test_claude_review_skips_events_sent_by_bots():
    """The action refuses bot-triggered runs (update-prs merges, release pull requests), so the job must not start."""
    spec = yaml.safe_load((REPO / ".github" / "workflows" / "claude-review.yml").read_text(encoding="utf-8"))
    condition = spec["jobs"]["review"]["if"]
    for clause in (
        "vars.CLAUDE_REVIEW == 'true'",
        "github.event.sender.type != 'Bot'",
        "!github.event.pull_request.draft",
        "github.event.pull_request.head.repo.full_name == github.repository",
    ):
        assert clause in condition


def test_claude_review_prefers_the_api_key():
    """Each review step authenticates with ANTHROPIC_API_KEY and uses the subscription token only without one."""
    spec = yaml.safe_load((REPO / ".github" / "workflows" / "claude-review.yml").read_text(encoding="utf-8"))
    actions = [step for step in spec["jobs"]["review"]["steps"] if "claude-code-action" in step.get("uses", "")]
    assert actions
    for step in actions:
        assert step["with"]["anthropic_api_key"] == "${{ secrets.ANTHROPIC_API_KEY }}"
        assert step["with"]["claude_code_oauth_token"].startswith("${{ secrets.ANTHROPIC_API_KEY == '' && ")


def _template_sync_module():
    import importlib.util

    path = REPO / "scripts" / "sync_template.py"
    spec = importlib.util.spec_from_file_location("sync_template", path)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _generated_plugin_ci() -> dict:
    text = _template_sync_module().ci_workflow()
    assert "notifier" not in text
    assert "changes" not in text.replace("upstream changes", "")
    return yaml.safe_load(text.replace("[[ package_name ]]", "x"))


def test_plugin_template_makefile_excludes_all_electron_only_targets():
    """Electron's install, sandbox and native-smoke targets stay out of generated plugin Makefiles."""
    generated = _template_sync_module().makefile()
    checked_in = (REPO / "template" / "Makefile").read_text(encoding="utf-8")
    assert generated == checked_in
    for target in ("desktop-electron-test", "desktop-electron-sandbox", "desktop-electron-native"):
        assert target not in generated
        assert target not in checked_in
    assert "chrome-sandbox" not in generated
    assert "sudo chown root:root" not in generated


def test_plugin_ci_has_no_core_only_jobs_and_every_need_exists():
    jobs = _generated_plugin_ci()["jobs"]
    for name, job in jobs.items():
        needs = job.get("needs", [])
        for need in [needs] if isinstance(needs, str) else needs:
            assert need in jobs, f"{name} needs {need}"
    assert jobs["setup"]["strategy"]["matrix"]["os"] == ["ubuntu-latest", "macos-latest"]
    assert "if" not in jobs["build"]


def test_pull_requests_run_one_macos_python_and_ci_ok_waits_for_changes():
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    include = jobs["test"]["strategy"]["matrix"]["include"]
    assert "pull_request" in include
    assert '[{"os":"macos-latest","python":"3.15"}]' in include
    assert {"changes", "notifier", "build"} <= set(jobs["ci-ok"]["needs"])


def test_electron_ci_runs_when_its_trigger_or_make_target_changes():
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    script = next(step["run"] for step in jobs["changes"]["steps"] if step.get("id") == "paths")
    assert "[ -f desktop/package.json ]" in script
    assert "desktop/" in script
    assert "Makefile$" in script
    assert "scripts/sync_template\\.py$" in script
    assert "\\.github/workflows/ci\\.yml$" in script
