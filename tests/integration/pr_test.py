"""scripts/pr.sh (`make pr`) against throwaway git repositories, with `uv`, `make` and `gh` stubbed out."""

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pr.sh"
URL = "https://github.com/example/widget/pull/7"
# Each stub logs its arguments to $LOG/<name>; gh answers `pr list` with $PR_URL when set, as if the PR existed.
STUBS = {
    "uv": 'printf "%s\\n" "$*" >> "$LOG/uv"',
    "make": (
        'printf "%s\\n" "$*" >> "$LOG/make"; git rev-parse HEAD >> "$LOG/preflight-head"; '
        'if [ "${FAIL_MAKE:-0}" = 1 ]; then exit 1; fi'
    ),
    "gh": f"""printf '%s\\n' "$@" > "$LOG/gh-$1-$2"
case "$1 $2" in
  "pr list") echo "${{PR_URL:-}}" ;;
  "pr create") echo {URL} ;;
esac""",
}
IDENTITY = {"NAME": "Ada Example", "EMAIL": "ada@example.com"}


def environment(root: Path) -> dict[str, str]:
    """Stubs first on PATH, and no git configuration but a made-up identity: the user's config can't leak in."""
    return {
        "PATH": f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(root),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        **{f"GIT_{who}_{key}": value for who in ("AUTHOR", "COMMITTER") for key, value in IDENTITY.items()},
        "LOG": str(root / "log"),
    }


def run_git(env: dict[str, str], cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True, timeout=30)
    return done.stdout.strip()


@pytest.fixture(scope="module")
def template(tmp_path_factory) -> Path:
    """A bare origin with `main` (one commit) and a clone of it, copied for each test: cloning costs a second."""
    root = tmp_path_factory.mktemp("template")
    env = environment(root)
    run_git(env, root, "init", "--quiet", "--bare", "--initial-branch=main", "origin.git")
    run_git(env, root, "clone", "--quiet", "origin.git", "work")
    work = root / "work"
    run_git(env, work, "switch", "--quiet", "--create", "main")
    (work / "base").write_text("base\n", encoding="utf-8")
    run_git(env, work, "add", "base")
    run_git(env, work, "commit", "--quiet", "--message", "feat: add base")
    run_git(env, work, "push", "--quiet", "origin", "main")
    run_git(env, work, "switch", "--quiet", "--create", "feat")
    return root


class Repo:
    """A clone of a bare `origin`, on branch `feat` branched from `main`."""

    def __init__(self, template: Path, tmp_path: Path) -> None:
        self.tmp = tmp_path
        for part in ("origin.git", "work"):
            shutil.copytree(template / part, tmp_path / part, symlinks=True)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        for name, body in STUBS.items():
            stub = bin_dir / name
            stub.write_text(f"#!/usr/bin/env bash\n{body}\nexit 0\n", encoding="utf-8")
            stub.chmod(0o755)
        (tmp_path / "log").mkdir()
        self.env = environment(tmp_path)
        self.dir = tmp_path / "work"
        self.git("remote", "set-url", "origin", str(tmp_path / "origin.git"))
        # A copy outside the repository: pr.sh puts <its repo>/.tools/bin first on PATH, where a real gh may be.
        self.script = tmp_path / "scripts" / "pr.sh"
        self.script.parent.mkdir()
        shutil.copy(SCRIPT, self.script)

    def git(self, *args: str) -> str:
        return run_git(self.env, self.dir, *args)

    def commit(self, name: str, content: str | None = None) -> str:
        (self.dir / name).write_text(f"{content or name}\n", encoding="utf-8")
        self.git("add", name)
        self.git("commit", "--quiet", "--message", f"feat: add {name}")
        return self.git("rev-parse", "HEAD")

    def push(self) -> None:
        self.git("push", "--quiet", "origin", "HEAD")

    def on_main(self, name: str) -> None:
        """Land a commit on origin/main, as a merged pull request would."""
        self.git("switch", "--quiet", "main")
        self.commit(name)
        self.git("push", "--quiet", "origin", "main")
        self.git("switch", "--quiet", "feat")

    def on_remote_only(self, change) -> None:
        """Make `change` on origin/feat only, as the update-prs workflow or a co-author would."""
        local = self.git("rev-parse", "HEAD")
        change()
        self.push()
        self.git("reset", "--quiet", "--hard", local)

    def rebase_onto_main(self) -> None:
        """What `make sync` does."""
        self.git("fetch", "--quiet", "origin")
        self.git("rebase", "--quiet", "origin/main")

    def remote(self) -> str:
        return self.git("ls-remote", "origin", "refs/heads/feat").split()[0]

    def pr(self, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.script), "feat: add widgets"],
            cwd=self.dir,
            env={**self.env, **env},
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def log(self, name: str) -> list[str]:
        path = self.tmp / "log" / name
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def created_body(self) -> str:
        """The --body that `gh pr create` got (it is the last argument but the optional --draft)."""
        args = self.log("gh-pr-create")
        return "\n".join(args[args.index("--body") + 1 :])


@pytest.fixture
def repo(template: Path, tmp_path: Path) -> Repo:
    return Repo(template, tmp_path)


def assert_stopped(repo: Repo, done: subprocess.CompletedProcess[str], remote: str) -> None:
    """Exit 2 before preflight, the remote branch untouched, the work tree clean, no rebase left in progress."""
    assert done.returncode == 2, done.stderr
    assert "have diverged" in done.stderr
    assert f"git push --force-with-lease=feat:{remote} origin feat" in done.stderr
    assert "git switch -c feat-2" in done.stderr
    assert repo.remote() == remote, "the script never force-pushes"
    assert repo.log("make") == [], "stops before spending a preflight"
    assert repo.git("status", "--porcelain") == ""
    assert not (repo.dir / ".git" / "rebase-merge").exists()


def merge_main(repo: Repo) -> None:
    repo.git("merge", "--quiet", "--no-edit", "main")


@pytest.mark.parametrize("remote_merged_main", [False, True], ids=["own commits only", "plus a merge of main"])
def test_stops_before_rebasing_a_branch_rebased_onto_main(repo, remote_merged_main):
    repo.commit("widget")
    repo.push()
    repo.on_main("gear")
    if remote_merged_main:
        repo.on_remote_only(lambda: merge_main(repo))
    repo.on_main("sprocket")
    repo.rebase_onto_main()
    remote = repo.remote()

    done = repo.pr()

    assert_stopped(repo, done, remote)
    assert "Every commit of origin/feat is in feat as an equivalent commit or merges main." in done.stderr


def test_the_suggested_force_push_works_and_make_pr_then_goes_through(repo):
    repo.commit("widget")
    repo.push()
    repo.on_main("gear")
    repo.rebase_onto_main()
    stopped = repo.pr()
    command = next(line for line in stopped.stderr.splitlines() if "--force-with-lease" in line)

    repo.git(*shlex.split(command)[1:])
    done = repo.pr()

    assert done.returncode == 0, done.stderr
    assert repo.remote() == repo.git("rev-parse", "HEAD")


def rewritten(repo: Repo) -> list[str]:
    """Pushed widget, then rebased onto main with a changed widget (a resolved conflict, an amend, a squash)."""
    repo.commit("widget")
    repo.push()
    repo.on_main("gear")
    repo.git("fetch", "--quiet", "origin")
    repo.git("reset", "--quiet", "--hard", "origin/main")
    repo.commit("widget", "widget, resolved")
    return ["feat: add widget"]


def dropped(repo: Repo) -> list[str]:
    """Pushed widget and bolt, then dropped bolt and rebased onto main."""
    repo.commit("widget")
    repo.commit("bolt")
    repo.push()
    repo.git("reset", "--quiet", "--hard", "HEAD~1")
    repo.on_main("gear")
    repo.rebase_onto_main()
    return ["feat: add bolt"]


def co_authored(repo: Repo) -> list[str]:
    """Someone pushed nut onto origin/feat, then the author rebased onto main without pulling it."""
    repo.commit("widget")
    repo.push()
    repo.on_remote_only(lambda: repo.commit("nut"))
    repo.on_main("gear")
    repo.rebase_onto_main()
    return ["feat: add nut"]


def side_branch_merged(repo: Repo) -> list[str]:
    """origin/feat merged a branch that is not main, then the author rebased onto main."""
    repo.commit("widget")
    repo.push()

    def merge_side() -> None:
        here = repo.git("rev-parse", "HEAD")
        repo.git("switch", "--quiet", "--create", "side", "main")
        repo.commit("spring")
        repo.git("switch", "--quiet", "feat")
        repo.git("merge", "--quiet", "--no-edit", "side")
        assert repo.git("rev-parse", "HEAD^") == here

    repo.on_remote_only(merge_side)
    repo.on_main("gear")
    repo.rebase_onto_main()
    return ["feat: add spring"]


@pytest.mark.parametrize("history", [rewritten, dropped, co_authored, side_branch_merged], ids=lambda f: f.__name__)
def test_stops_and_lists_what_a_force_push_would_drop(repo, history):
    subjects = history(repo)
    remote = repo.remote()

    done = repo.pr()

    assert_stopped(repo, done, remote)
    listed = re.findall(r"^ {6}[0-9a-f]+ (.*)$", done.stderr, flags=re.MULTILINE)
    assert listed == subjects


def test_builds_on_a_merge_of_main_the_branch_does_not_have(repo):
    repo.commit("widget")
    repo.push()
    repo.on_main("gear")
    repo.on_remote_only(lambda: merge_main(repo))
    merged = repo.remote()
    merge_main(repo)  # caught up with main by merging, not rebasing
    repo.commit("bolt")

    done = repo.pr()

    assert done.returncode == 0, done.stderr
    assert repo.git("merge-base", "--is-ancestor", merged, "HEAD") == ""
    assert repo.git("merge-base", "--is-ancestor", "origin/main", "HEAD") == ""
    assert repo.log("preflight-head") == [repo.git("rev-parse", "HEAD")]
    assert repo.remote() == repo.git("rev-parse", "HEAD")


def test_merges_commits_someone_else_pushed_before_preflight(repo):
    repo.commit("widget")
    repo.push()
    repo.on_remote_only(lambda: repo.commit("nut"))
    repo.commit("bolt")
    theirs = repo.remote()

    done = repo.pr()

    assert done.returncode == 0, done.stderr
    assert repo.git("merge-base", "--is-ancestor", theirs, "HEAD") == ""
    assert repo.log("preflight-head") == [repo.git("rev-parse", "HEAD")]
    assert repo.remote() == repo.git("rev-parse", "HEAD")


def test_preserves_local_merge_of_main_when_remote_is_already_an_ancestor(repo):
    repo.commit("widget")
    repo.push()
    repo.on_main("gear")
    merge_main(repo)
    head = repo.git("rev-parse", "HEAD")

    done = repo.pr()

    assert done.returncode == 0, done.stderr
    assert repo.remote() == head, "the existing merge must not be flattened"
    assert repo.git("merge-base", "--is-ancestor", "origin/main", "HEAD") == ""


def test_merge_conflict_stops_before_preflight_and_push(repo):
    repo.commit("widget")
    repo.push()
    repo.on_remote_only(lambda: repo.commit("widget", "remote edit"))
    remote = repo.remote()
    repo.commit("widget", "local edit")

    done = repo.pr()

    assert done.returncode != 0
    assert repo.remote() == remote
    assert repo.log("make") == []
    assert repo.log("gh-pr-create") == []


def test_failed_preflight_stops_before_push(repo):
    repo.commit("widget")
    repo.push()
    remote = repo.remote()
    repo.commit("bolt")

    done = repo.pr(FAIL_MAKE="1")

    assert done.returncode != 0
    assert repo.remote() == remote
    assert repo.log("gh-pr-create") == []


def test_pushes_a_branch_that_fast_forwards_after_preflight(repo):
    repo.commit("widget")
    repo.push()
    head = repo.commit("bolt")

    done = repo.pr()

    assert done.returncode == 0, done.stderr
    assert repo.remote() == head
    assert repo.log("make") == ["--no-print-directory preflight BASE=origin/main"]
    assert f"✓ {URL} (review and checks required before merging)" in done.stdout
    assert repo.log("gh-pr-merge") == [], "the script must not turn on auto-merge"


@pytest.mark.parametrize(
    ("env", "body"),
    [
        ({}, "- feat: add widget\n- feat: add bolt"),
        ({"BODY": "Widgets."}, "Widgets."),
        ({"BODY_FILE": "../body.md"}, "## Why\n\nWidgets."),
    ],
    ids=["commit subjects", "BODY", "BODY_FILE"],
)
def test_the_description_of_a_new_pull_request(repo, env, body):
    (repo.tmp / "body.md").write_text("## Why\n\nWidgets.\n", encoding="utf-8")
    repo.commit("widget")
    repo.commit("bolt")

    assert repo.pr(**env).returncode == 0
    assert repo.created_body() == body
    assert f"--quiet --script scripts/check_confidentiality.py --text {body}" in "\n".join(repo.log("uv"))


@pytest.mark.parametrize(
    ("env", "error"),
    [
        ({"BODY_FILE": "missing.md"}, "BODY_FILE=missing.md: no such readable file."),
        ({"BODY_FILE": ".git"}, "BODY_FILE=.git: no such readable file."),
        ({"BODY_FILE": "../empty.md"}, "BODY_FILE=../empty.md is empty."),
        # The file exists, so this fails on BODY being set too, not on the file.
        ({"BODY_FILE": ".git/HEAD", "BODY": "x"}, "Set BODY or BODY_FILE, not both."),
    ],
    ids=["missing", "directory", "empty", "both"],
)
def test_body_mistakes_stop_before_anything_happens(repo, env, error):
    (repo.tmp / "empty.md").write_text(" \n\n", encoding="utf-8")
    repo.commit("widget")

    done = repo.pr(**env)

    assert done.returncode == 2
    assert error in done.stderr
    assert repo.log("uv") == []
    assert repo.git("ls-remote", "origin", "refs/heads/feat") == ""


def test_an_existing_pull_request_keeps_its_description_and_says_so(repo):
    repo.commit("widget")

    done = repo.pr(BODY="Widgets.", PR_URL=URL)

    assert done.returncode == 0, done.stderr
    assert "its description stays" in done.stderr
    assert repo.log("gh-pr-create") == []
    assert repo.log("gh-pr-edit") == ["pr", "edit", URL, "--title", "feat: add widgets"]
