"""scripts/pr.sh (`make pr`) against throwaway git repositories, with `uv`, `make` and `gh` stubbed out."""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pr.sh"
URL = "https://github.com/example/widget/pull/7"
# Each stub logs its arguments to $LOG/<name>; gh answers `pr list` with $PR_URL when set, as if the PR existed.
STUBS = {
    "uv": 'printf "%s\\n" "$*" >> "$LOG/uv"',
    "make": 'printf "%s\\n" "$*" >> "$LOG/make"',
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
    assert repo.git("rev-parse", "HEAD^") == merged, "bolt is replayed onto the remote's merge of main"
    assert repo.remote() == repo.git("rev-parse", "HEAD")


def test_rebases_onto_commits_someone_else_pushed(repo):
    repo.commit("widget")
    repo.push()
    repo.on_remote_only(lambda: repo.commit("nut"))
    repo.commit("bolt")
    theirs = repo.remote()

    done = repo.pr()

    assert done.returncode == 0, done.stderr
    assert repo.git("rev-parse", "HEAD^") == theirs
    assert repo.remote() == repo.git("rev-parse", "HEAD")


def test_pushes_a_branch_that_fast_forwards_after_preflight(repo):
    repo.commit("widget")
    repo.push()
    head = repo.commit("bolt")

    done = repo.pr()

    assert done.returncode == 0, done.stderr
    assert repo.remote() == head
    assert repo.log("make") == ["--no-print-directory preflight BASE=origin/main"]
    assert f"✓ {URL} (squash auto-merge on)" in done.stdout


@pytest.mark.parametrize("mixed", [False, True])
def test_actual_pr_selects_owning_preflight_for_plugin_only_changes_and_preserves_core_checks(repo, mixed):
    helper = repo.dir / "scripts/scoped_checks.py"
    helper.parent.mkdir()
    repo.commit("scripts/scoped_checks.py", (SCRIPT.parent / "scoped_checks.py").read_text())
    repo.git("push", "--quiet", "origin", "HEAD:main")
    repo.git("fetch", "--quiet", "origin", "main")
    (repo.dir / "plugins/heinzel-harbor").mkdir(parents=True)
    repo.commit("plugins/heinzel-harbor/feature.py", "value = 1")
    if mixed:
        repo.commit("core.py", "core = True")
    # Run the real scope helper and Git diff, but capture SDK and make invocations instead of testing Core.
    (repo.tmp / "bin/uv").write_text(
        f"#!{sys.executable}\nimport json,os,subprocess,sys\nfrom pathlib import Path\n"
        "args=sys.argv[1:]\n"
        "with (Path(os.environ['LOG'])/'scope').open('a') as stream: stream.write(json.dumps(args)+'\\n')\n"
        "if 'scripts/scoped_checks.py' in args:\n"
        "    index=args.index('scripts/scoped_checks.py')\n"
        "    sys.exit(subprocess.call([sys.executable,*args[index:]]))\n"
        "if 'hz-plugin' in args and 'plan' in args:\n"
        "    print(json.dumps({'include':[{'name':'heinzel-harbor'},{'name':'heinzel-reader'}]}))\n"
    )
    done = repo.pr()
    assert done.returncode == 0, done.stderr
    calls = [json.loads(line) for line in repo.log("scope")]
    owning = [call[call.index("hz-plugin") + 1 :] for call in calls if "hz-plugin" in call]
    if mixed:
        assert repo.log("make") == ["preflight BASE=origin/main"]
        assert owning == []
    else:
        assert repo.log("make") == ["commits secrets BASE=origin/main"]
        assert ["run", "--script", "scripts/check_confidentiality.py"] in calls
        assert owning == [
            ["plan", "--changed", "plugins/heinzel-harbor/feature.py"],
            ["preflight", "heinzel-harbor"],
            ["preflight", "heinzel-reader"],
        ]


@pytest.mark.parametrize("change", ["deleted-plugin", "untracked-plugin", "core"])
def test_core_root_agent_scope_uses_owning_checks_including_deletions_and_untracked_files(repo, change):
    (repo.dir / "scripts").mkdir()
    helper = repo.dir / "scripts/scoped_checks.py"
    repo.commit("scripts/scoped_checks.py", (SCRIPT.parent / "scoped_checks.py").read_text())
    (repo.dir / "plugins/heinzel-harbor").mkdir(parents=True)
    repo.commit("plugins/heinzel-harbor/obsolete.py", "value = 1")
    if change == "deleted-plugin":
        (repo.dir / "plugins/heinzel-harbor/obsolete.py").unlink()
    else:
        path = "plugins/heinzel-harbor/new.py" if change == "untracked-plugin" else "core.py"
        (repo.dir / path).write_text("value = 2\n")
    done = subprocess.run(
        [sys.executable, str(helper), "fast"],
        cwd=repo.dir,
        env=repo.env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    if change == "core":
        assert repo.log("make") == ["check-fast BASE=origin/main"]
    else:
        assert repo.log("make") == []
        assert any("hz-plugin check-changed --changed plugins/heinzel-harbor/" in call for call in repo.log("uv"))


@pytest.mark.parametrize("core_host", [False, True])
@pytest.mark.parametrize("filename", ["feature.py", "feature name.py", "föö.py"])
def test_actual_agent_stop_hook_dispatches_scoped_checks_only_in_core_hosts(repo, core_host, filename):
    if core_host:
        (repo.dir / "scripts").mkdir()
        repo.commit("scripts/scoped_checks.py", (SCRIPT.parent / "scoped_checks.py").read_text())
    (repo.dir / "plugins/heinzel-harbor").mkdir(parents=True)
    (repo.dir / "plugins/heinzel-harbor" / filename).write_text("value = 1\n")
    hook = SCRIPT.parents[1] / ".claude/hooks/check_before_stop.py"
    done = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps({"cwd": str(repo.dir)}),
        cwd=repo.dir,
        env=repo.env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    if core_host:
        assert repo.log("uv") == [f"run --no-project --script {repo.dir}/scripts/scoped_checks.py fast"]
        assert repo.log("make") == []
    else:
        assert repo.log("make") == ["check-fast"]


@pytest.mark.parametrize("conflict", [False, True], ids=["clean merge", "resolved conflict"])
def test_preserves_a_pushed_branch_after_merging_main(repo, conflict):
    repo.commit("widget")
    repo.push()
    repo.commit("base", "feature")
    repo.git("switch", "--quiet", "main")
    repo.commit("base" if conflict else "gear", "main changes")
    repo.git("push", "--quiet", "origin", "main")
    repo.git("switch", "--quiet", "feat")
    merged = subprocess.run(
        ["git", "merge", "--no-edit", "main"], cwd=repo.dir, env=repo.env, capture_output=True, check=False, timeout=30
    )
    assert merged.returncode == (1 if conflict else 0)
    if conflict:
        (repo.dir / "base").write_text("feature and main\n", encoding="utf-8")
        repo.git("add", "base")
        repo.git("commit", "--quiet", "--no-edit")
    head = repo.git("rev-parse", "HEAD")

    done = repo.pr()

    assert done.returncode == 0, done.stderr
    assert repo.remote() == head, "an ordinary push must preserve the resolved merge and commit identities"
    assert (repo.dir / "base").read_text(encoding="utf-8") == ("feature and main\n" if conflict else "feature\n")
    assert repo.git("status", "--porcelain") == ""
    assert repo.log("make") == ["--no-print-directory preflight BASE=origin/main"]


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
