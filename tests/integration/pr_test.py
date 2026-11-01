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
    "uv": (
        'printf "%s\\n" "$*" >> "$LOG/uv"; '
        'case "$*" in *scripts/scoped_checks.py*) git rev-parse HEAD >> "$LOG/preflight-head";; esac'
    ),
    "make": (
        'printf "%s\\n" "$*" >> "$LOG/make"; git rev-parse HEAD >> "$LOG/preflight-head"; '
        'if [ "${FAIL_MAKE:-0}" = 1 ]; then exit 1; fi; '
        'if [ "${DIRTY_MAKE:-0}" = 1 ]; then printf changed > base; fi; '
        'if [ "${COMMIT_MAKE:-0}" = 1 ]; then printf changed > base; git add base; '
        'git commit --quiet -m "feat: modify during preflight"; fi'
    ),
    "gh": f"""printf '%s\\n' "$@" > "$LOG/gh-$1-$2"
case "$1 $2" in
  "repo view")
    if [ "${{FAIL_REPO:-0}}" = 1 ]; then exit 1; fi
    if [ -n "${{CANONICAL_REPO:-}}" ]; then echo "$CANONICAL_REPO"; else
      git remote get-url origin | sed -E 's#/+$##; s#\\.git$##; s#^.*[:/]([^/:]+/[^/:]+)$#\\1#'
    fi ;;
  "pr list")
    if [ "${{FAIL_LIST:-0}}" = 1 ]; then exit 1; fi
    if [ -n "${{PR_DATA:-}}" ]; then
      filter=""; base=""; shift 2
      while [ "$#" -gt 0 ]; do
        case "$1" in --jq) filter="$2"; shift 2 ;; --base) base="$2"; shift 2 ;; *) shift ;; esac
      done
      if [ "$base" = main ]; then
        printf '%s' "$PR_DATA" | jq '[.[] | select(.baseRefName == "main")][:101]' | jq -r "$filter" || exit 1
      else
        printf '%s' "$PR_DATA" | jq -r "$filter" || exit 1
      fi
    else echo "${{PR_URL:-}}"; fi ;;

  "pr view") if [ "${{FAIL_VIEW:-0}}" = 1 ]; then exit 1; fi; echo "${{AUTO_STATE-disabled}}" ;;
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


def test_scoped_preflight_observes_integrated_remote_commit(repo):
    (repo.dir / "scripts").mkdir()
    repo.commit("scripts/scoped_checks.py", "# fixture")
    repo.git("push", "--quiet", "origin", "HEAD:main")
    repo.git("fetch", "--quiet", "origin", "main")
    repo.commit("widget")
    repo.push()
    repo.on_remote_only(lambda: repo.commit("nut"))
    repo.commit("bolt")

    done = repo.pr()

    assert done.returncode == 0, done.stderr
    assert repo.log("preflight-head") == [repo.remote()]
    assert repo.log("gh-pr-merge") == []


def test_dirty_after_preflight_stops_before_push(repo):
    repo.commit("widget")
    repo.push()
    remote = repo.remote()
    repo.commit("bolt")

    done = repo.pr(DIRTY_MAKE="1")

    assert done.returncode == 2
    assert "changed after preflight" in done.stderr
    assert repo.remote() == remote
    assert repo.log("gh-pr-create") == []


def test_committed_change_after_preflight_stops_before_push(repo):
    repo.commit("widget")
    repo.push()
    remote = repo.remote()
    repo.commit("bolt")

    done = repo.pr(COMMIT_MAKE="1")

    assert done.returncode == 2
    assert "changed after preflight" in done.stderr
    assert repo.git("status", "--porcelain") == ""
    assert repo.remote() == remote
    assert repo.log("gh-pr-create") == []


@pytest.mark.skipif(shutil.which("jq") is None, reason="the fixture needs jq to exercise the real selector")
def test_existing_main_pr_matches_owner_case_and_ignores_other_bases(repo):
    repo.commit("widget")
    repo.push()
    remote = repo.remote()
    repo.commit("bolt")
    origin = Path(repo.git("remote", "get-url", "origin"))
    origin_name = f"{origin.parent.name}/{origin.stem}"
    rows = [
        {
            "url": "https://github.com/example/widget/pull/9",
            "headRepository": {"nameWithOwner": origin_name},
            "baseRefName": "other",
        },
        {"url": URL, "headRepository": {"nameWithOwner": origin_name.upper()}, "baseRefName": "main"},
    ]

    done = repo.pr(PR_DATA=json.dumps(rows), AUTO_STATE="enabled")

    assert done.returncode == 2, done.stderr
    assert repo.remote() == remote
    assert URL in repo.log("gh-pr-view")
    args = repo.log("gh-pr-list")
    assert args[args.index("--base") + 1] == "main"
    assert args[args.index("--limit") + 1] == "101"


@pytest.mark.skipif(shutil.which("jq") is None, reason="the fixture needs jq to exercise the real selector")
def test_same_owner_different_repository_does_not_hide_matching_pr(repo):
    repo.commit("widget")
    repo.push()
    remote = repo.remote()
    repo.commit("bolt")
    origin = Path(repo.git("remote", "get-url", "origin"))
    rows = [
        {
            "url": "https://github.com/example/widget/pull/9",
            "headRepository": {"nameWithOwner": f"{origin.parent.name}/other"},
            "baseRefName": "main",
        },
        {"url": URL, "headRepository": {"nameWithOwner": f"{origin.parent.name}/{origin.stem}"}, "baseRefName": "main"},
    ]

    done = repo.pr(PR_DATA=json.dumps(rows), AUTO_STATE="enabled")

    assert done.returncode == 2, done.stderr
    assert repo.remote() == remote
    assert URL in repo.log("gh-pr-view")


@pytest.mark.skipif(shutil.which("jq") is None, reason="the fixture needs jq to exercise the real selector")
def test_redirected_origin_uses_canonical_repository_identity(repo):
    repo.commit("widget")
    repo.push()
    remote = repo.remote()
    repo.commit("bolt")
    rows = [{"url": URL, "headRepository": {"nameWithOwner": "Canonical/Real"}, "baseRefName": "main"}]

    done = repo.pr(PR_DATA=json.dumps(rows), CANONICAL_REPO="Canonical/Real", AUTO_STATE="enabled")

    assert done.returncode == 2, done.stderr
    assert repo.remote() == remote
    assert URL in repo.log("gh-pr-view")


def test_failed_origin_resolution_is_refused_before_push(repo):
    repo.commit("widget")
    repo.push()
    remote = repo.remote()
    repo.commit("bolt")

    done = repo.pr(FAIL_REPO="1")

    assert done.returncode == 2
    assert "Cannot verify the origin repository" in done.stderr
    assert repo.remote() == remote


@pytest.mark.skipif(shutil.which("jq") is None, reason="the fixture needs jq to exercise the real selector")
def test_missing_head_repository_is_refused_before_push(repo):
    repo.commit("widget")
    repo.push()
    remote = repo.remote()
    repo.commit("bolt")
    rows = [{"url": URL, "headRepository": None, "baseRefName": "main"}]

    done = repo.pr(PR_DATA=json.dumps(rows))

    assert done.returncode != 0
    assert repo.remote() == remote
    assert repo.log("gh-pr-merge") == []


@pytest.mark.skipif(shutil.which("jq") is None, reason="the fixture needs jq to exercise the real selector")
@pytest.mark.parametrize("invalid", ["empty-name", "bad-name", "empty-url", "missing-url"])
def test_malformed_pr_metadata_is_refused_before_push(repo, invalid):
    repo.commit("widget")
    repo.push()
    remote = repo.remote()
    repo.commit("bolt")
    origin = Path(repo.git("remote", "get-url", "origin"))
    item = {
        "url": URL,
        "headRepository": {"nameWithOwner": f"{origin.parent.name}/{origin.stem}"},
        "baseRefName": "main",
    }
    if invalid == "empty-name":
        item["headRepository"]["nameWithOwner"] = ""
    elif invalid == "bad-name":
        item["headRepository"]["nameWithOwner"] = "invalid name"
    elif invalid == "empty-url":
        item["url"] = ""
    else:
        del item["url"]

    done = repo.pr(PR_DATA=json.dumps([item]))

    assert done.returncode != 0
    assert repo.remote() == remote
    assert repo.log("gh-pr-merge") == []


@pytest.mark.skipif(shutil.which("jq") is None, reason="the fixture needs jq to exercise the real selector")
def test_excessive_pr_search_is_refused_before_push(repo):
    repo.commit("widget")
    repo.push()
    remote = repo.remote()
    repo.commit("bolt")
    rows = [{"url": URL, "headRepository": {"nameWithOwner": "other/repo"}, "baseRefName": "main"}] * 101

    done = repo.pr(PR_DATA=json.dumps(rows))

    assert done.returncode != 0
    assert repo.remote() == remote
    assert repo.log("gh-pr-merge") == []


@pytest.mark.parametrize("state", ["enabled", "unknown", ""])
def test_existing_auto_merge_is_refused_before_push(repo, state):
    repo.commit("widget")
    repo.push()
    remote = repo.remote()
    repo.commit("bolt")

    done = repo.pr(PR_URL=URL, AUTO_STATE=state)

    assert done.returncode == 2
    assert "Auto-merge is enabled or unknown" in done.stderr
    assert repo.remote() == remote
    assert repo.log("gh-pr-merge") == []


def test_pr_lookup_failure_is_refused_before_push(repo):
    repo.commit("widget")
    repo.push()
    remote = repo.remote()
    repo.commit("bolt")

    done = repo.pr(FAIL_LIST="1")

    assert done.returncode != 0
    assert repo.remote() == remote
    assert repo.log("gh-pr-merge") == []


def test_auto_merge_lookup_failure_is_refused_before_push(repo):
    repo.commit("widget")
    repo.push()
    remote = repo.remote()
    repo.commit("bolt")

    done = repo.pr(PR_URL=URL, FAIL_VIEW="1")

    assert done.returncode == 2
    assert "Cannot verify" in done.stderr
    assert repo.remote() == remote
    assert repo.log("gh-pr-merge") == []


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


def install_scope_toolkit_stub(repo):
    """Log the external toolkit boundary without downloading or running plugin checks."""
    repo.commit(
        "scripts/plugin_tooling.py",
        "import json,os,sys\nfrom pathlib import Path\n"
        "args=sys.argv[1:]\n"
        "with (Path(os.environ['LOG'])/'scope').open('a') as stream: stream.write(json.dumps(args)+'\\n')\n"
        "if 'plan' in args: print(json.dumps({'include':[{'name':'heinzel-harbor'},{'name':'heinzel-reader'}]}))\n",
    )
    repo.commit("scripts/check_confidentiality.py", (SCRIPT.parent / "check_confidentiality.py").read_text())


@pytest.mark.parametrize("mixed", [False, True])
def test_actual_pr_selects_owning_preflight_for_plugin_only_changes_and_preserves_core_checks(repo, mixed):
    helper = repo.dir / "scripts/scoped_checks.py"
    helper.parent.mkdir()
    repo.commit("scripts/scoped_checks.py", (SCRIPT.parent / "scoped_checks.py").read_text())
    install_scope_toolkit_stub(repo)
    repo.git("push", "--quiet", "origin", "HEAD:main")
    repo.git("fetch", "--quiet", "origin", "main")
    base = repo.git("rev-parse", "origin/main")
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
        assert repo.log("make") == [f"preflight BASE={base}"]
        assert owning == []
    else:
        assert repo.log("make") == [f"commits secrets BASE={base}"]
        assert "Confidentiality check skipped" in done.stdout
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
    install_scope_toolkit_stub(repo)
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
        assert repo.log("make") == ["check-fast"]
    else:
        assert repo.log("make") == []
        assert any(
            json.loads(call)[-3:-1] == ["check-changed", "--changed"]
            and json.loads(call)[-1].startswith("plugins/heinzel-harbor/")
            for call in repo.log("scope")
        )


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
    assert f"--quiet --frozen --no-sync --no-build python scripts/check_confidentiality.py --text {body}" in "\n".join(
        repo.log("uv")
    )


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


@pytest.mark.parametrize("attack", ["git-option", "make-expression"])
def test_scoped_preflight_rejects_untrusted_base_before_make(repo, attack):
    (repo.dir / "scripts").mkdir()
    helper = repo.dir / "scripts/scoped_checks.py"
    helper.write_text((SCRIPT.parent / "scoped_checks.py").read_text())
    sentinel = repo.tmp / "must-not-exist"
    base = f"--output={sentinel}" if attack == "git-option" else f"$(shell touch {sentinel})"
    done = subprocess.run(
        [sys.executable, str(helper), "preflight", "--base=" + base],
        cwd=repo.dir,
        env=repo.env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert done.returncode == 2, done.stderr
    assert "valid commit revision" in done.stderr
    assert repo.log("make") == []
    assert not sentinel.exists()


@pytest.mark.parametrize("reference", ["origin/main", "main", "HEAD~1"])
def test_scoped_preflight_passes_only_canonical_commit_to_make(repo, reference):
    (repo.dir / "scripts").mkdir()
    helper = repo.dir / "scripts/scoped_checks.py"
    repo.commit("scripts/scoped_checks.py", (SCRIPT.parent / "scoped_checks.py").read_text())
    repo.commit("core.py", "changed = True")
    base = repo.git("rev-parse", reference)
    done = subprocess.run(
        [sys.executable, str(helper), "preflight", "--base", reference],
        cwd=repo.dir,
        env=repo.env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    assert repo.log("make") == [f"preflight BASE={base}"]


def test_a_branch_behind_main_without_conflicts_goes_through(repo):
    """Main's own CI gates a release (ADR 0114), so queued pushes must not loop on `make sync`."""
    repo.commit("widgets")
    repo.on_main("elsewhere")

    result = repo.pr()

    assert result.returncode == 0, result.stderr
    assert "Behind" not in result.stderr
    assert repo.remote() == repo.git("rev-parse", "HEAD")


def test_a_branch_that_conflicts_with_main_stops_before_pushing(repo):
    repo.commit("widgets", "ours")
    repo.git("switch", "--quiet", "main")
    repo.commit("widgets", "theirs")
    repo.git("push", "--quiet", "origin", "main")
    repo.git("switch", "--quiet", "feat")

    result = repo.pr()

    assert result.returncode == 2
    assert "Conflicts with origin/main; run make sync first." in result.stderr
    assert repo.git("ls-remote", "origin", "refs/heads/feat") == ""
