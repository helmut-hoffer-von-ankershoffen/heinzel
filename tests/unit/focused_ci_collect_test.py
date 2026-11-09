"""The protected collector accepts only a fresh exact GitHub attempt and reviewed Git objects."""

import copy
import hashlib
import importlib
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

pytestmark = pytest.mark.unit
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
BASE, HEAD, MERGE, TREE = (char * 40 for char in "abcd")
WORKFLOW = ".github/workflows/focused.yml"
SELECTOR = "scripts/focused_ci_receipt.py"
RAW = b":100644 100644 first second M\0tests/unit/one_test.py\0"


class World:
    """The GitHub API and Git objects for one reviewed run."""

    def __init__(self, policy, object_ids=(BASE, HEAD, MERGE, TREE), raw=RAW):
        base_sha, self.head, merge, tree = object_ids
        self.policy = policy
        self.workflow = {"id": 42, "path": WORKFLOW, "state": "active"}
        base = {"ref": "main", "repo": {"id": 1}, "sha": base_sha}
        head = {"ref": "branch", "repo": {"id": 1}, "sha": self.head}
        self.pull = {
            "number": 7,
            "state": "open",
            "draft": False,
            "base": base,
            "head": head,
            "merge_commit_sha": merge,
        }
        self.run = {
            "id": 51,
            "run_attempt": 2,
            "workflow_id": 42,
            "path": WORKFLOW,
            "repository": {"id": 1},
            "head_repository": {"id": 1},
            "head_sha": self.head,
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "pull_requests": [{"number": 7, "base": copy.deepcopy(base), "head": copy.deepcopy(head)}],
            "check_suite_id": 54,
        }
        self.latest = copy.deepcopy(self.run)
        self.runs = [self.latest]
        self.suite = {
            "id": 54,
            "app": {"id": 11},
            "head_sha": self.head,
            "status": "completed",
            "conclusion": "success",
        }
        self.jobs = [
            {
                "id": n,
                "run_id": 51,
                "run_attempt": 2,
                "name": name,
                "head_sha": self.head,
                "status": "completed",
                "conclusion": "success",
            }
            for n, name in enumerate(("A", "B", "C"), 100)
        ]
        self.checks = {
            job["id"]: {
                **job,
                "app": {"id": 11},
                "check_suite": {"id": 54},
            }
            for job in self.jobs
        }
        self.calls = []
        self.answers = {
            "pulls/7": self.pull,
            "git/ref/heads/main": {"object": {"sha": base_sha}},
            "git/ref/pull/7/merge": {"object": {"sha": merge}},
            "actions/workflows/42": self.workflow,
            "actions/runs/51/attempts/2": self.run,
            "check-suites/54": self.suite,
            **{f"check-runs/{n}": check for n, check in self.checks.items()},
        }
        self.objects = {
            ("merge-base", "--is-ancestor", base_sha, self.head): b"",
            ("rev-list", "--parents", "-n", "1", merge): f"{merge} {base_sha} {self.head}".encode(),
            ("rev-parse", f"{merge}^{{tree}}"): tree.encode(),
            ("rev-parse", f"{self.head}^{{tree}}"): tree.encode(),
            (
                "diff-tree",
                "--no-commit-id",
                "-r",
                "--raw",
                "--no-abbrev",
                "--no-renames",
                "-z",
                base_sha,
                self.head,
            ): raw,
            **{
                ("cat-file", "blob", f"{commit}:{path}"): value
                for commit in (base_sha, merge)
                for path, value in ((WORKFLOW, b"workflow"), (SELECTOR, b"selector"))
            },
        }
        policy.write_text(
            f"""
[source]
repository_id = 1
workflow_id = 42
workflow_path = "{WORKFLOW}"
workflow_sha256 = "{hashlib.sha256(b"workflow").hexdigest()}"
selector_sha256 = "{hashlib.sha256(b"selector").hexdigest()}"
workload_app_id = 11

[reviewed]
base = "{base_sha}"
head = "{self.head}"
diff_sha256 = "{hashlib.sha256(raw).hexdigest()}"
jobs = ["A", "B", "C"]
""",
            encoding="utf-8",
        )
        self.policy_sha256 = hashlib.sha256(policy.read_bytes()).hexdigest()

    def read(self, endpoint):
        self.calls.append(endpoint)
        parsed = urlsplit(endpoint)
        if not parsed.query:
            return copy.deepcopy(self.answers[parsed.path])
        query = parse_qs(parsed.query)
        if parsed.path == "actions/workflows/42/runs":
            assert query["head_sha"] == [self.head]
            assert query["event"] == ["pull_request"]
            key, rows = "workflow_runs", self.runs
        else:
            assert parsed.path == "actions/runs/51/attempts/2/jobs"
            key, rows = "jobs", self.jobs
        size = int(query["per_page"][0])
        page = int(query["page"][0])
        return {"total_count": len(rows), key: copy.deepcopy(rows[(page - 1) * size : page * size])}

    def git(self, *args):
        return self.objects[args]


@pytest.fixture
def case(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    collector = importlib.import_module("focused_ci_collect")
    monkeypatch.setattr(collector, "PAGE_SIZE", 2)
    world = World(tmp_path / "policy.toml")
    return collector, world


def collect(case, read=None):
    collector, world = case
    return collector.collect(
        read or world.read,
        world.policy,
        collector.Trigger(7, world.head, 51, 2),
        policy_sha256=world.policy_sha256,
        git=world.git,
    )


def test_collects_all_pages_of_the_exact_attempt(case):
    receipt = collect(case)
    assert receipt.run.merge == MERGE
    assert receipt.run.tree == TREE
    assert receipt.jobs == (("A", "success"), ("B", "success"), ("C", "success"))
    assert sum("/attempts/2/jobs?" in call for call in case[1].calls) == 2


def test_authenticates_detached_policy_before_reading_github(case):
    world = case[1]
    world.policy.write_text(world.policy.read_text() + "\n# Changed after approval\n")
    with pytest.raises(ValueError, match="does not match its protected digest"):
        collect(case)
    assert world.calls == []


def test_an_authenticated_policy_for_an_older_base_cannot_admit_the_current_head(case):
    world = case[1]
    live_base = "e" * 40
    world.answers["git/ref/heads/main"]["object"]["sha"] = world.pull["base"]["sha"] = live_base
    with pytest.raises(ValueError, match="exact base and head"):
        collect(case)


def test_detached_policy_collects_against_real_git_commits(case, monkeypatch, tmp_path):
    collector, _ = case
    # A Git hook exports repository-local variables that survive chdir, including GIT_DIR in linked worktrees.
    git_env = subprocess.check_output(["git", "rev-parse", "--local-env-vars"], text=True, timeout=10)
    for name in git_env.splitlines():
        monkeypatch.delenv(name, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
            timeout=10,
        ).strip()

    git("init", "--quiet", "--template=")
    for path, contents in ((WORKFLOW, b"workflow"), (SELECTOR, b"selector")):
        file = repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(contents)
    git("add", ".")
    base_tree = git("write-tree").decode()
    base = git("commit-tree", base_tree, "-m", "Protected base").decode()
    (repo / "change.txt").write_text("Reviewed change\n")
    git("add", "change.txt")
    tree = git("write-tree").decode()
    head = git("commit-tree", tree, "-p", base, "-m", "Reviewed head").decode()
    merge = git("commit-tree", tree, "-p", base, "-p", head, "-m", "Test merge").decode()
    raw = collector.git_bytes(
        "diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", base, head
    )
    world = World(tmp_path / "external-policy.toml", (base, head, merge, tree), raw)

    receipt = collector.collect(
        world.read,
        world.policy,
        collector.Trigger(7, head, 51, 2),
        policy_sha256=world.policy_sha256,
    )

    assert world.policy.parent != repo
    assert b"policy.toml" not in git("ls-tree", "-r", "--name-only", base)
    assert (receipt.run.base, receipt.run.head, receipt.run.merge, receipt.run.tree) == (base, head, merge, tree)
    assert receipt.jobs == (("A", "success"), ("B", "success"), ("C", "success"))
    assert sum("/attempts/2/jobs?" in call for call in world.calls) == 2


@pytest.mark.parametrize(
    ("target", "field", "value", "message"),
    [
        ("workflow", "path", ".github/workflows/decoy.yml", "registered workflow"),
        ("run", "workflow_id", 43, "workflow identity"),
        ("run", "event", "workflow_dispatch", "workflow identity"),
        ("run", "status", "in_progress", "workflow identity"),
        ("run", "run_attempt", 1, "different run attempt"),
        ("latest", "run_attempt", 3, "newer run or attempt"),
        ("latest", "id", 52, "newer run or attempt"),
        ("pull", "merge_commit_sha", "1" * 40, "merge ref changed"),
        ("pull", "draft", True, "merge ref changed"),
        ("suite", "app", {"id": 12}, "workload publisher"),
    ],
)
def test_rejects_stale_or_wrong_identity(case, target, field, value, message):
    getattr(case[1], target)[field] = value
    with pytest.raises(ValueError, match=message):
        collect(case)


@pytest.mark.parametrize("kind", ["omitted_page", "changed_total", "duplicate_id", "duplicate_name", "skipped"])
def test_rejects_partial_or_unsuccessful_job_sets(case, kind):
    world = case[1]

    def read(path):
        result = world.read(path)
        if "/jobs?" in path and path.endswith("&page=2"):
            if kind == "omitted_page":
                result["jobs"] = []
            elif kind == "changed_total":
                result["total_count"] = 4
        return result

    if kind == "duplicate_id":
        world.jobs[2]["id"] = world.jobs[0]["id"]
    elif kind == "duplicate_name":
        world.jobs[2]["name"] = world.checks[102]["name"] = "A"
    elif kind == "skipped":
        world.jobs[2]["conclusion"] = world.checks[102]["conclusion"] = "skipped"

    with pytest.raises(ValueError, match=r"incomplete|Duplicate|unexpected jobs|did not succeed"):
        collect(case, read)


@pytest.mark.parametrize(("field", "value"), [("app", {"id": 12}), ("check_suite", {"id": 55}), ("head_sha", "1" * 40)])
def test_rejects_a_job_check_from_another_publisher_suite_or_head(case, field, value):
    case[1].checks[102][field] = value
    with pytest.raises(ValueError, match="different attempt, commit, check suite or publisher"):
        collect(case)


@pytest.mark.parametrize("kind", ["run", "attempt", "base", "head", "merge"])
def test_rechecks_freshness_after_reading_jobs(case, kind):
    world = case[1]

    def read(path):
        if path == "check-runs/102":
            if kind == "run":
                world.runs.append({**world.latest, "id": 52})
            elif kind == "attempt":
                world.latest["run_attempt"] = 3
            elif kind == "base":
                world.answers["git/ref/heads/main"]["object"]["sha"] = "1" * 40
            elif kind == "head":
                world.pull["head"]["sha"] = "1" * 40
            elif kind == "merge":
                world.answers["git/ref/pull/7/merge"]["object"]["sha"] = "1" * 40
        return world.read(path)

    with pytest.raises(ValueError, match="changed"):
        collect(case, read)


@pytest.mark.parametrize(
    "key",
    [
        ("cat-file", "blob", f"{BASE}:{WORKFLOW}"),
        ("cat-file", "blob", f"{MERGE}:{SELECTOR}"),
        ("rev-parse", f"{MERGE}^{{tree}}"),
    ],
)
def test_rejects_unreviewed_workflow_selector_or_merge_tree(case, key):
    case[1].objects[key] = b"changed"
    with pytest.raises(ValueError, match=r"tree|bytes"):
        collect(case)
