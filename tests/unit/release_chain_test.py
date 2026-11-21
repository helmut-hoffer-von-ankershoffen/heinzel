"""The CI release chain keeps the release rules: first-attempt cut, frozen main, unmoved base, marker before Latest."""

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_chain", ROOT / "scripts/release_chain.py")
assert SPEC
assert SPEC.loader
chain = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = chain
SPEC.loader.exec_module(chain)

TIP, BASE, HEAD, OTHER = (c * 40 for c in "abcd")
WHEEL, PROFILE = "e" * 64, "f" * 64


def reader(routes):
    def read(path):
        for prefix, value in routes.items():
            if path == prefix or path.startswith(prefix + "?"):
                return value
        return None

    return read


def release(version, *, marker=True, prerelease=False, wheel=WHEEL):
    assets = [
        {"name": f"heinzel-{version}-py3-none-any.whl", "digest": f"sha256:{wheel}"},
        {"name": "artifact-manifest.json", "digest": "sha256:" + "1" * 64},
    ]
    if marker:
        assets.append({"name": chain.MARKER, "digest": "sha256:" + "2" * 64})
    return {"tag_name": f"v{version}", "draft": False, "prerelease": prerelease, "assets": assets}


def pr(number, ref, title=None, sha=HEAD, repo=chain.REPOSITORY):
    return {
        "number": number,
        "title": title if title is not None else f"chore(release): v{ref.removeprefix('release/v')}",
        "head": {"ref": ref, "sha": sha, "repo": {"full_name": repo}},
    }


def run_row(event="push", status="completed", conclusion="success", attempt=1, run_id=7):
    return {"id": run_id, "event": event, "status": status, "conclusion": conclusion, "run_attempt": attempt}


def routes(*, prs=(), rels=None, runs=None, commits=None):
    rels = rels if rels is not None else [release("0.9.53"), release("0.9.52")]
    found = {
        "pulls": list(prs),
        "releases": rels,
        f"actions/workflows/ci.yml/runs?head_sha={TIP}&per_page=100": {
            "workflow_runs": runs if runs is not None else [run_row()]
        },
        f"compare/v0.9.53...{TIP}": {
            "total_commits": len(commits if commits is not None else [1]),
            "commits": [{"commit": {"message": m}} for m in (commits if commits is not None else ["fix(web): a (#1)"])],
        },
    }
    for rel in rels:
        found[f"releases/tags/{rel['tag_name']}"] = rel
    return found


def test_bump_kind_takes_only_the_next_patch_minor_or_major():
    assert chain.bump_kind("0.9.53", "0.9.54") == "patch"
    assert chain.bump_kind("0.9.53", "0.10.0") == "minor"
    assert chain.bump_kind("0.9.53", "1.0.0") == "major"
    with pytest.raises(chain.ChainError):
        chain.bump_kind("0.9.53", "0.9.56")
    with pytest.raises(chain.ChainError):
        chain.bump_kind("0.9.53", "v0.9.54")


@pytest.mark.parametrize(
    ("rows", "error"),
    [
        ([run_row(conclusion="failure")], chain.ChainError),
        ([run_row(attempt=2)], chain.ChainError),  # green only on a rerun: no known-flake exception
        ([run_row(status="in_progress", conclusion=None)], chain.Wait),
        ([], chain.Wait),
        ([run_row(), run_row(run_id=8)], chain.ChainError),
        ([run_row(), run_row(event="workflow_dispatch", conclusion="failure", run_id=9)], chain.ChainError),
    ],
)
def test_the_cut_needs_main_green_on_the_first_attempt(rows, error):
    with pytest.raises(error):
        chain.main_gate(reader(routes(runs=rows)), TIP)


def test_a_green_tip_with_a_running_macos_lane_may_be_cut():
    lane = run_row(event="workflow_dispatch", status="in_progress", conclusion=None, run_id=9)
    chain.main_gate(reader(routes(runs=[run_row(), lane])), TIP)


def test_plan_cuts_the_next_patch_from_a_green_tip_with_an_intro():
    result = chain.plan(reader(routes()), "", auto=False, tip=TIP)
    assert result["phase"] == "cut"
    assert result["version"] == "0.9.54"
    assert result["bump"] == "patch"
    assert result["base"] == TIP
    assert result["intro"] == "A."


def test_plan_resumes_an_open_release_pr_and_refuses_another_version():
    read = reader(routes(prs=[pr(9, "release/v0.9.54"), pr(10, "fix/x", title="fix: x")]))
    assert chain.plan(read, "", auto=False, tip=TIP)["phase"] == "candidate"
    assert chain.plan(read, "", auto=False, tip=TIP)["pr"] == "9"
    with pytest.raises(chain.ChainError, match="in flight"):
        chain.plan(read, "0.9.55", auto=False, tip=TIP)


def test_two_open_release_prs_refuse():
    read = reader(routes(prs=[pr(9, "release/v0.9.54"), pr(11, "release/v0.9.55")]))
    with pytest.raises(chain.ChainError, match="more than one"):
        chain.plan(read, "", auto=False, tip=TIP)


def test_a_fork_or_mistitled_release_branch_is_no_release_pr():
    read = reader(routes(prs=[pr(9, "release/v0.9.54", repo="someone/fork"), pr(10, "release/v0.9.54", title="x")]))
    assert chain.open_release_prs(read) == []


def test_plan_resumes_a_published_release_without_marker_at_its_tail():
    rels = [release("0.9.54", marker=False), release("0.9.53")]
    result = chain.plan(reader(routes(rels=rels)), "", auto=False, tip=TIP)
    assert (result["phase"], result["version"]) == ("tail", "0.9.54")


def test_no_cut_while_the_previous_release_is_not_terminal():
    # A Latest-less release is still final; without its marker it is the tail in flight, never cut past.
    rels = [release("0.9.53", marker=False)]
    assert chain.plan(reader(routes(rels=rels)), "", auto=False, tip=TIP)["phase"] == "tail"
    with pytest.raises(chain.ChainError, match="in flight"):
        chain.plan(reader(routes(rels=rels)), "0.9.54", auto=False, tip=TIP)


def test_auto_cut_skips_when_nothing_releasable():
    read = reader(routes(commits=["docs: a (#1)", "test: b (#2)"]))
    assert chain.plan(read, "", auto=True, tip=TIP)["phase"] == "idle"
    assert chain.plan(read, "", auto=False, tip=TIP)["phase"] == "cut"


def test_a_done_version_is_reported_and_an_old_one_refused():
    assert chain.plan(reader(routes()), "0.9.53", auto=False, tip=TIP)["phase"] == "done"
    with pytest.raises(chain.ChainError, match="not newer"):
        chain.plan(
            reader(routes(rels=[release("0.9.53"), release("0.9.52", marker=False)])), "0.9.51", auto=False, tip=TIP
        )


def test_intro_names_combine_members_in_order():
    message = "chore: combine (#9)\n\nCombines #5 fix(web): first\nCombines #6 feat(ui): second\n  wrapped\n"
    assert chain.commit_subjects(message) == ["fix(web): first", "feat(ui): second wrapped"]
    assert chain.intro_from(["fix(web): first (#5)", "docs: x", "feat: b"]) == "First; B."
    assert chain.intro_from(["docs: x"]) == "Reliability and maintenance fixes."


def test_the_notes_must_name_every_commit_and_every_combine_member():
    notes = f"- a ([abc]({TIP})) ([#5](u))"
    commits = [
        (TIP, "fix: a (#1)"),
        (BASE, "fix: b (#2)"),
        (OTHER, "chore: c\n\nCombines #5 fix: x\nCombines #6 fix: y"),
        (HEAD, "chore(release): v0.9.53 (#3)"),
    ]
    assert chain.missing_from_notes(commits, notes) == [f"{BASE[:9]} fix: b (#2)", f"#6 (in {OTHER[:9]})"]


def test_the_freeze_fails_every_other_pr_while_a_candidate_is_open():
    read = reader(routes(prs=[pr(9, "release/v0.9.54"), pr(10, "fix/x", title="fix: x", sha=OTHER)]))
    targets, candidate = chain.freeze_targets(read)
    assert candidate == 9
    assert targets == [(9, HEAD, True), (10, OTHER, False)]
    assert chain.freeze_of_pr(read, 10)[0] is False
    assert chain.freeze_of_pr(read, 9)[0] is True
    posted = []
    chain.freeze_sync(read, lambda sha, passes, text: posted.append((sha, passes)))
    assert posted == [(HEAD, True), (OTHER, False)]


def test_no_freeze_without_a_candidate():
    read = reader(routes(prs=[pr(10, "fix/x", title="fix: x", sha=OTHER)]))
    assert chain.freeze_targets(read) == ([(10, OTHER, True)], None)
    assert chain.freeze_of_pr(read, 10)[0] is True


def test_the_merge_refuses_when_main_moved_since_the_candidate_base():
    read = reader({f"commits/{HEAD}": {"parents": [{"sha": BASE}]}, "commits/main": {"sha": OTHER}})
    assert "main moved" in chain.main_moved(read, HEAD)
    read = reader({f"commits/{HEAD}": {"parents": [{"sha": BASE}]}, "commits/main": {"sha": BASE}})
    assert chain.main_moved(read, HEAD) is None
    with pytest.raises(chain.ChainError, match="one parent"):
        chain.main_moved(reader({f"commits/{HEAD}": {"parents": []}}), HEAD)


def content(files=None, commits=None, subject="chore(release): v0.9.54"):
    files = (
        files
        if files is not None
        else [("pyproject.toml", "modified"), ("uv.lock", "modified"), ("CHANGELOG.md", "modified")]
    )
    commits = commits if commits is not None else [{"sha": HEAD, "commit": {"message": f"{subject}\n\nbody"}}]
    return {
        "total_commits": len(commits),
        "commits": commits,
        "files": [{"filename": n, "status": st} for n, st in files],
    }


def content_reader(data):
    return reader({f"commits/{HEAD}": {"parents": [{"sha": BASE}]}, f"compare/{BASE}...{HEAD}": data})


def test_the_release_pr_holds_exactly_the_release_commit():
    chain.verify_pr_content(content_reader(content()), pr(9, "release/v0.9.54"), "0.9.54")


@pytest.mark.parametrize(
    ("data", "match"),
    [
        (
            content(files=[("pyproject.toml", "modified"), ("uv.lock", "modified"), ("src/x.py", "modified")]),
            "src/x.py",
        ),
        (content(files=[("pyproject.toml", "modified"), ("uv.lock", "modified"), ("CHANGELOG.md", "added")]), "added"),
        (content(files=[("CHANGELOG.md", "modified")]), "no version and lock"),
        (
            content(
                commits=[{"sha": OTHER, "commit": {"message": "fix: x"}}, {"sha": HEAD, "commit": {"message": "x"}}]
            ),
            "exactly one commit",
        ),
        (content(subject="chore(release): v0.9.55"), "titled"),
        (None, "cannot compare"),
    ],
)
def test_a_release_pr_with_anything_else_is_refused(data, match):
    with pytest.raises(chain.ChainError, match=match):
        chain.verify_pr_content(content_reader(data), pr(9, "release/v0.9.54"), "0.9.54")


def test_merge_checks_the_pr_content_before_anything_else(monkeypatch):
    monkeypatch.setattr(chain, "run", lambda *_, **__: pytest.fail("merged a PR with foreign content"))
    data = content(files=[("pyproject.toml", "modified"), ("uv.lock", "modified"), ("src/x.py", "modified")])
    read = reader(
        {
            "pulls/9": {"merged": False, **pr(9, "release/v0.9.54")},
            f"commits/{HEAD}": {"parents": [{"sha": BASE}]},
            "commits/main": {"sha": BASE},
            f"compare/{BASE}...{HEAD}": data,
        }
    )
    with pytest.raises(chain.ChainError, match="more than the release files"):
        chain.merge(read, 9, "0.9.54", "token")


def test_a_real_release_needs_the_owner_token(monkeypatch, capsys):
    monkeypatch.setenv("HAS_RELEASE_TOKEN", "false")
    assert chain.main(["need-token"]) == 1
    assert "RELEASE_TOKEN" in capsys.readouterr().out
    monkeypatch.setenv("HAS_RELEASE_TOKEN", "true")
    assert chain.main(["need-token"]) == 0


def test_merge_never_runs_gh_when_main_moved(monkeypatch):
    calls = []
    monkeypatch.setattr(chain, "run", lambda argv, **_: calls.append(argv) or "")
    read = reader(
        {
            "pulls/9": {"merged": False, **pr(9, "release/v0.9.54")},
            f"commits/{HEAD}": {"parents": [{"sha": BASE}]},
            "commits/main": {"sha": OTHER},
            f"compare/{BASE}...{HEAD}": content(),
        }
    )
    with pytest.raises(chain.ChainError, match="main moved"):
        chain.merge(read, 9, "0.9.54", "token")
    assert calls == []


def test_merge_is_pinned_to_the_verified_head(monkeypatch):
    calls = []
    state = {"merged": False}

    def fake_run(argv, **_):
        calls.append(argv)
        state["merged"] = True
        return ""

    monkeypatch.setattr(chain, "run", fake_run)

    def read(path):
        if path == "pulls/9":
            return {**pr(9, "release/v0.9.54"), "merged": state["merged"], "merge_commit_sha": TIP}
        return {
            f"commits/{HEAD}": {"parents": [{"sha": BASE}]},
            "commits/main": {"sha": BASE},
            f"commits/{TIP}": {"parents": [{"sha": BASE}]},
            f"compare/{BASE}...{HEAD}": content(),
        }.get(path)

    assert chain.merge(read, 9, "0.9.54", "token") == TIP
    assert "--match-head-commit" in calls[0]
    assert calls[0][calls[0].index("--match-head-commit") + 1] == HEAD


def app_release(version, profile_bytes, wheel=WHEEL):
    name = f"heinzel-{version}-py3-none-any.whl"
    return {
        "tag_name": f"app-v{version}",
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": "standard.json", "digest": "sha256:" + hashlib.sha256(profile_bytes).hexdigest()},
            {"name": name, "digest": f"sha256:{wheel}"},
        ],
    }


def profile(version, wheel=WHEEL):
    name = f"heinzel-{version}-py3-none-any.whl"
    url = f"https://github.com/{chain.PLUGINS}/releases/download/app-v{version}/{name}"
    data = {
        "format": 2,
        "sdk": 1,
        "core_version": version,
        "artifacts": [{"name": "heinzel", "version": version, "filename": name, "sha256": wheel, "url": url}],
    }
    return json.dumps(data).encode()


def test_the_profile_must_select_the_released_core_wheel():
    content = profile("0.9.54")
    app = app_release("0.9.54", content)
    assert chain.validate_profile(content, "0.9.54", WHEEL, app) == hashlib.sha256(content).hexdigest()
    other = profile("0.9.54", wheel="9" * 64)
    other_app = app_release("0.9.54", other, wheel="9" * 64)
    with pytest.raises(chain.ChainError, match="another Core wheel"):
        chain.validate_profile(other, "0.9.54", WHEEL, other_app)
    with pytest.raises(chain.ChainError, match="provider digest"):
        chain.validate_profile(content + b" ", "0.9.54", WHEEL, app)


def test_snapshot_waits_for_pypi_and_refuses_a_different_pypi_wheel(monkeypatch):
    content = profile("0.9.54")
    read = reader({"releases/tags/v0.9.54": release("0.9.54", marker=False), "commits/v0.9.54": {"sha": TIP}})
    app = app_release("0.9.54", content)
    monkeypatch.setattr(chain, "pypi_wheel_digest", lambda _: None)
    with pytest.raises(chain.Wait, match="PyPI"):
        chain.snapshot(read, lambda _: app, "0.9.54", lambda _: content)
    monkeypatch.setattr(chain, "pypi_wheel_digest", lambda _: "0" * 64)
    with pytest.raises(chain.ChainError, match="differs"):
        chain.snapshot(read, lambda _: app, "0.9.54", lambda _: content)
    monkeypatch.setattr(chain, "pypi_wheel_digest", lambda _: WHEEL)
    snap = chain.snapshot(read, lambda _: app, "0.9.54", lambda _: content)
    assert snap["wheel_sha256"] == WHEEL
    assert snap["commit"] == TIP


def receipt(os_name, *, verdict="ACCEPTED", run_id="5", attempt="1", wheel=WHEEL):
    return {
        "verdict": verdict,
        "public_path_proof": True,
        "ci": {"repository": chain.REPOSITORY, "run_id": run_id, "run_attempt": attempt, "runner_os": os_name},
        "published": {
            "expect_version": "0.9.54",
            "expect_app_version": "0.9.54",
            "core_wheel_sha256": wheel,
            "profile_sha256": PROFILE,
        },
    }


def write_receipts(folder, **overrides):
    for os_name, artifact in chain.RECEIPTS.items():
        (folder / artifact).mkdir(parents=True)
        data = receipt(os_name, **overrides.get(os_name, {}))
        (folder / artifact / "result.json").write_text(json.dumps(data))


SNAP = {"version": "0.9.54", "wheel_sha256": WHEEL, "profile_sha256": PROFILE}


def test_both_first_attempt_receipts_accept(tmp_path):
    write_receipts(tmp_path)
    assert chain.judge(tmp_path, SNAP, "5", "1")[0] is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"Linux": {"verdict": "REJECTED"}},
        {"macOS": {"wheel": "0" * 64}},
        {"macOS": {"run_id": "6"}},
    ],
)
def test_one_bad_receipt_rejects(tmp_path, overrides):
    write_receipts(tmp_path, **overrides)
    assert chain.judge(tmp_path, SNAP, "5", "1")[0] is False


def test_a_missing_receipt_is_incomplete_and_a_rerun_never_counts(tmp_path):
    with pytest.raises(chain.ChainError, match="no readable receipt"):
        chain.judge(tmp_path, SNAP, "5", "1")
    write_receipts(tmp_path, Linux={"attempt": "2"}, macOS={"attempt": "2"})
    with pytest.raises(chain.ChainError, match="first attempt"):
        chain.judge(tmp_path, SNAP, "5", "2")


def test_latest_only_after_the_marker_never_backwards(monkeypatch):
    calls = []
    monkeypatch.setattr(chain, "run", lambda argv, **_: calls.append(argv) or "")
    newer = reader({"releases/tags/v0.9.54": release("0.9.54"), "releases/latest": {"tag_name": "v0.9.55"}})
    with pytest.raises(chain.ChainError, match="newer"):
        chain.promote(newer, "0.9.54", SNAP)
    unmarked = reader(
        {"releases/tags/v0.9.54": release("0.9.54", marker=False), "releases/latest": {"tag_name": "v0.9.53"}}
    )
    with pytest.raises(chain.ChainError, match="marker"):
        chain.promote(unmarked, "0.9.54", SNAP)
    changed = reader(
        {"releases/tags/v0.9.54": release("0.9.54", wheel="0" * 64), "releases/latest": {"tag_name": "v0.9.53"}}
    )
    with pytest.raises(chain.ChainError, match="wheel differs"):
        chain.promote(changed, "0.9.54", SNAP)
    assert calls == []


def test_the_marker_is_written_once(monkeypatch):
    monkeypatch.setattr(chain, "run", lambda *_, **__: pytest.fail("no upload over an existing marker"))
    with pytest.raises(chain.ChainError, match="terminal"):
        chain.publish_marker(reader({"releases/tags/v0.9.54": release("0.9.54")}), "0.9.54", {})
    data = chain.marker("0.9.54", "5", good=True)
    assert (data["project"], data["version"], data["accepted"], data["acceptance_run"]) == (
        "heinzel",
        "0.9.54",
        True,
        5,
    )


def git(cwd, *args):
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True, timeout=60)
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "user.email", "t@example.invalid")
    for name in ("pyproject.toml", "uv.lock", "CHANGELOG.md", "README.md"):
        (tmp_path / name).write_text("0\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "fix: base")
    git(tmp_path, "switch", "-qc", "release/v0.9.54")
    return tmp_path, git(tmp_path, "rev-parse", "HEAD")


def release_commit(cwd, *files, subject="chore(release): v0.9.54"):
    for name in files:
        (cwd / name).write_text("1\n")
    git(cwd, "commit", "-qam", subject)


def test_scope_accepts_only_the_release_files(repo):
    cwd, base = repo
    release_commit(cwd, "pyproject.toml", "uv.lock", "CHANGELOG.md")
    assert chain.verify_scope(cwd, base, "0.9.54") == git(cwd, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("files", "subject", "match"),
    [
        (("pyproject.toml", "uv.lock", "README.md"), "chore(release): v0.9.54", "outside"),
        (("pyproject.toml",), "chore(release): v0.9.54", "no version and lock"),
        (("pyproject.toml", "uv.lock"), "chore(release): v0.9.55", "titled"),
    ],
)
def test_scope_refuses_anything_else(repo, files, subject, match):
    cwd, base = repo
    release_commit(cwd, *files, subject=subject)
    with pytest.raises(chain.ChainError, match=match):
        chain.verify_scope(cwd, base, "0.9.54")


def test_scope_refuses_two_commits_or_another_base(repo):
    cwd, base = repo
    release_commit(cwd, "pyproject.toml", "uv.lock")
    release_commit(cwd, "CHANGELOG.md")
    with pytest.raises(chain.ChainError, match="one commit"):
        chain.verify_scope(cwd, base, "0.9.54")


def test_audit_notes_reads_the_new_section(repo):
    cwd, base = repo
    git(cwd, "tag", "v0.9.53", base)
    (cwd / "README.md").write_text("2\n")
    git(cwd, "commit", "-qam", "fix: later")
    later = git(cwd, "rev-parse", "HEAD")
    (cwd / "CHANGELOG.md").write_text(f"## [0.9.54]\n\n- later ({later})\n\n## [0.9.53]\n")
    chain.audit_notes(cwd, "0.9.53", later, "0.9.54")
    (cwd / "CHANGELOG.md").write_text(f"## [0.9.54]\n\n- none\n\n## [0.9.53]\n- {later}\n")
    with pytest.raises(chain.ChainError, match="do not name"):
        chain.audit_notes(cwd, "0.9.53", later, "0.9.54")
