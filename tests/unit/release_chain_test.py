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
    return {
        "id": run_id,
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "run_attempt": attempt,
        "head_sha": TIP,
        "head_branch": "main",
        "path": ".github/workflows/ci.yml",
        "repository": {"full_name": chain.REPOSITORY},
        "head_repository": {"full_name": chain.REPOSITORY},
        "display_title": f"CI macOS main {TIP}" if event == "workflow_dispatch" else "CI",
    }


def macos_run(**kwargs):
    return run_row(event="workflow_dispatch", run_id=9, **kwargs)


def jobs_for(run_id, runner):
    candidate = chain.load_candidate_module()
    required = chain.main_workload(candidate, runner)
    jobs = [
        {
            "id": index,
            "run_id": run_id,
            "head_sha": TIP,
            "run_attempt": 1,
            "status": "completed",
            "name": name,
            "conclusion": "skipped" if name == candidate.QUARANTINE_IDLE else "success",
            "runner_id": 123,
            "steps": [
                {"name": step, "status": "completed", "conclusion": "success"}
                for step in (required.get(name) or ("Run workload",))
            ],
        }
        for index, name in enumerate([*required, candidate.QUARANTINE_PLAN, candidate.QUARANTINE_IDLE], 1)
    ]
    return {"total_count": len(jobs), "jobs": jobs}


def tail_run(version, status="in_progress"):
    return {"id": 3, "status": status, "display_title": f"Release tail v{version}"}


def routes(*, prs=(), rels=None, runs=None, commits=None, tails=()):
    rels = rels if rels is not None else [release("0.9.53"), release("0.9.52")]
    runs = runs if runs is not None else [run_row(), macos_run()]
    found = {
        "pulls": list(prs),
        "actions/workflows/release-tail.yml/runs?per_page=100": {"workflow_runs": list(tails)},
        "releases": rels,
        f"actions/workflows/ci.yml/runs?head_sha={TIP}&per_page=100": {"workflow_runs": runs, "total_count": len(runs)},
        "actions/runs/7/attempts/1/jobs": jobs_for(7, "ubuntu-24.04"),
        "actions/runs/9/attempts/1/jobs": jobs_for(9, "macos-latest"),
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


def test_a_green_tip_with_a_running_macos_lane_waits():
    lane = macos_run(status="in_progress", conclusion=None)
    with pytest.raises(chain.Wait, match="macOS dispatch"):
        chain.main_gate(reader(routes(runs=[run_row(), lane])), TIP)


def test_main_proof_names_the_exact_two_original_runs():
    assert chain.main_gate(reader(routes()), TIP) == chain.MainCIProof(TIP, 7, 9)


@pytest.mark.parametrize(
    ("rows", "error"),
    [
        ([run_row()], chain.Wait),
        ([run_row(), macos_run() | {"head_sha": OTHER}], chain.ChainError),
        ([run_row(), macos_run(attempt=2)], chain.ChainError),
        ([run_row(), macos_run(conclusion="cancelled")], chain.ChainError),
        ([run_row(), macos_run() | {"display_title": "CI"}], chain.Wait),
        ([run_row(), macos_run(), macos_run() | {"id": 10}], chain.ChainError),
    ],
)
def test_main_requires_exact_coalesced_original_dispatch(rows, error):
    with pytest.raises(error):
        chain.main_gate(reader(routes(runs=rows)), TIP)


@pytest.mark.parametrize("damage", ["run-pages", "job-pages", "runnerless", "skipped-step", "wrong-attempt", "missing"])
def test_main_refuses_incomplete_or_nonexecuting_jobs(damage):
    data = routes()
    page = data["actions/runs/9/attempts/1/jobs"]
    job = next(j for j in page["jobs"] if j["name"] == "End-to-end web-3 (macos-latest)")
    if damage == "run-pages":
        data[f"actions/workflows/ci.yml/runs?head_sha={TIP}&per_page=100"]["total_count"] += 1
    elif damage == "job-pages":
        page["total_count"] += 1
    elif damage == "runnerless":
        job.update(runner_id=0, steps=[], conclusion="cancelled")
    elif damage == "skipped-step":
        job["steps"][0]["conclusion"] = "skipped"
    elif damage == "wrong-attempt":
        job["run_attempt"] = 2
    else:
        page["jobs"].remove(job)
        page["total_count"] -= 1
    with pytest.raises(chain.ChainError):
        chain.main_gate(reader(data), TIP)


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
    done = [tail_run("0.9.54", status="completed")]  # a stopped tail is resumed, not cut past
    assert chain.plan(reader(routes(rels=rels, tails=done)), "", auto=False, tip=TIP)["phase"] == "tail"


def test_a_tail_workflow_not_yet_on_main_has_no_running_run():
    assert chain.tail_running(reader({}), "0.9.54") is False
    with pytest.raises(chain.ChainError, match="cannot read"):
        chain.tail_running(reader({"actions/workflows/release-tail.yml/runs": {"oops": 1}}), "0.9.54")


def test_no_cut_past_a_tail_that_is_not_running():
    # A Latest-less release is still final; without its marker and a tail run it is resumed first.
    rels = [release("0.9.53", marker=False)]
    assert chain.plan(reader(routes(rels=rels)), "", auto=False, tip=TIP)["phase"] == "tail"
    with pytest.raises(chain.ChainError, match="in flight"):
        chain.plan(reader(routes(rels=rels)), "0.9.54", auto=False, tip=TIP)


def test_the_next_release_is_cut_while_the_previous_tail_runs():
    """Pipelining: N in its tail (tag published, tail run going) leaves main open; N+1 is cut from the tip."""
    rels = [release("0.9.53", marker=False), release("0.9.52")]
    read = reader(routes(rels=rels, tails=[tail_run("0.9.53")]))
    result = chain.plan(read, "", auto=False, tip=TIP)
    assert (result["phase"], result["version"], result["previous"]) == ("cut", "0.9.54", "0.9.53")
    assert "pipelined behind v0.9.53" in result["reason"]
    # the freeze is only for an open release PR: N's tail freezes nothing
    assert chain.freeze_targets(reader(routes(rels=rels, prs=[pr(10, "fix/x", title="fix: x")])))[1] is None


def test_never_more_than_two_releases_in_flight():
    rels = [release("0.9.54", marker=False), release("0.9.53", marker=False), release("0.9.52")]
    tails = [tail_run("0.9.54"), tail_run("0.9.53")]
    with pytest.raises(chain.ChainError, match="at most 2 in flight"):
        chain.plan(reader(routes(rels=rels, tails=tails)), "", auto=False, tip=TIP)
    # an open N+1 release PR while N is in its tail resumes N+1's candidate, it never cuts a third
    read = reader(routes(rels=rels[1:], tails=tails[1:], prs=[pr(9, "release/v0.9.54")]))
    assert chain.plan(read, "", auto=False, tip=TIP)["phase"] == "candidate"


def test_a_newer_verdict_waits_for_the_older_tail_and_absorbs_a_rejected_one():
    rels = [release("0.9.54", marker=False), release("0.9.53", marker=False), release("0.9.52")]
    with pytest.raises(chain.Wait, match=r"v0\.9\.53 is still in its tail"):
        chain.older_open(reader(routes(rels=rels)), "0.9.54")
    chain.older_open(reader(routes(rels=rels)), "0.9.53")  # the older one never waits for the newer
    # the older one is terminal (its marker says accepted or not): the newer one goes ahead and absorbs it
    terminal = [release("0.9.54", marker=False), release("0.9.53"), release("0.9.52")]
    chain.older_open(reader(routes(rels=terminal)), "0.9.54")


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
        chain.merge(read, 9, "0.9.54", "token", CANDIDATE_PROOF)


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
        chain.merge(read, 9, "0.9.54", "token", CANDIDATE_PROOF)
    assert calls == []


RULES = [
    {"type": "pull_request", "parameters": {}},
    {
        "type": "required_status_checks",
        "parameters": {"required_status_checks": [{"context": "CI OK"}, {"context": "SonarCloud"}]},
    },
]


def check(name, status="completed", conclusion="success", run_id=1):
    return {"name": name, "status": status, "conclusion": conclusion, "id": run_id}


def checks_reader(runs, statuses=()):
    return reader(
        {
            "rules/branches/main": RULES,
            f"commits/{HEAD}/check-runs?per_page=100&page=1": {"check_runs": list(runs)},
            f"commits/{HEAD}/status": {"statuses": list(statuses)},
        }
    )


def test_the_merge_waits_for_exactly_the_required_checks_not_the_merge_state():
    sonar = {"context": "SonarCloud", "state": "success"}
    required = chain.required_checks(checks_reader([]))
    assert required == ["CI OK", "SonarCloud"]
    chain.checks_pass(checks_reader([check("CI OK"), check("other", conclusion="failure")], [sonar]), HEAD, required)
    chain.checks_pass(checks_reader([check("CI OK", conclusion="neutral")], [sonar]), HEAD, required)
    with pytest.raises(chain.Wait, match="SonarCloud"):
        chain.checks_pass(checks_reader([check("CI OK")]), HEAD, required)
    with pytest.raises(chain.Wait, match="CI OK"):
        chain.checks_pass(
            checks_reader([check("CI OK", status="in_progress", conclusion=None)], [sonar]), HEAD, required
        )
    newest_red = [check("CI OK"), check("CI OK", conclusion="failure", run_id=2)]
    with pytest.raises(chain.ChainError, match="failed"):
        chain.checks_pass(checks_reader(newest_red, [sonar]), HEAD, required)
    with pytest.raises(chain.ChainError, match="failed"):
        chain.checks_pass(
            checks_reader([check("CI OK")], [{"context": "SonarCloud", "state": "error"}]), HEAD, required
        )


def test_rules_without_required_checks_refuse_the_merge():
    with pytest.raises(chain.ChainError, match="unguarded"):
        chain.required_checks(reader({"rules/branches/main": [{"type": "pull_request"}]}))
    with pytest.raises(chain.ChainError, match="cannot read"):
        chain.required_checks(reader({}))


@pytest.mark.parametrize(
    ("changed", "refusal"),
    [
        (None, None),
        ("head", "head differs"),
        ("run", "first-attempt run moved"),
        ("tree", "merged with a different tree"),
    ],
)
def test_merge_is_pinned_to_the_verified_candidate(monkeypatch, changed, refusal):
    calls = []
    state = {"merged": changed == "tree"}

    def fake_run(argv, **_):
        calls.append(argv)
        state["merged"] = True
        return ""

    monkeypatch.setattr(chain, "run", fake_run)
    monkeypatch.setattr(
        chain,
        "verify_candidate",
        lambda *_: {"head": HEAD, "tree": TREE, "run_id": 78 if changed == "run" else 77},
    )

    def read(path):
        if path == "pulls/9":
            pull = pr(9, "release/v0.9.54")
            if changed == "head":
                pull["head"]["sha"] = OTHER
            return {**pull, "merged": state["merged"], "merge_commit_sha": TIP}
        return {
            f"commits/{HEAD}": {"parents": [{"sha": BASE}]},
            "commits/main": {"sha": BASE},
            f"commits/{TIP}": {
                "parents": [{"sha": BASE}],
                "commit": {"tree": {"sha": OTHER if changed == "tree" else TREE}},
            },
            f"compare/{BASE}...{HEAD}": content(),
            "rules/branches/main": RULES,
            f"commits/{HEAD}/check-runs?per_page=100&page=1": {"check_runs": [check("CI OK")]},
            f"commits/{HEAD}/status": {"statuses": [{"context": "SonarCloud", "state": "success"}]},
        }.get(path)

    if refusal:
        with pytest.raises(chain.ChainError, match=refusal):
            chain.merge(read, 9, "0.9.54", "token", CANDIDATE_PROOF)
        assert calls == []
        return
    assert chain.merge(read, 9, "0.9.54", "token", CANDIDATE_PROOF) == TIP
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
    shards = {"public": ["1"], "update": ["2"], "plugins": ["3"], "browser": ["4"]}
    return {
        "schema": 2,
        "verdict": verdict,
        "public_path_proof": True,
        "expectation_supplied": True,
        "sharding": {"shards": shards, "proof_roots": {name: f"proof/{name}" for name in shards}},
        "ci": {"repository": chain.REPOSITORY, "run_id": run_id, "run_attempt": attempt, "runner_os": os_name},
        "published": {
            "expect_version": "0.9.54",
            "expect_app_version": "0.9.54",
            "core_wheel_sha256": wheel,
            "profile_sha256": PROFILE,
        },
    }


def write_receipts(folder, *, edit=None, **overrides):
    for os_name, artifact in chain.RECEIPTS.items():
        (folder / artifact).mkdir(parents=True)
        data = receipt(os_name, **overrides.get(os_name, {}))
        if edit and os_name == "macOS":
            edit(data)
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


@pytest.mark.parametrize(
    "edit",
    [
        lambda r: r.pop("sharding"),  # a serial receipt: acceptance must be the sharded workflow
        lambda r: r.update(schema=1),
        lambda r: r["sharding"]["proof_roots"].pop("browser"),  # a shard without its own proof
        lambda r: r.update(expectation_supplied=False),  # pinned to whatever was published, not the snapshot
    ],
    ids=["serial", "old-schema", "shard-without-proof", "unpinned"],
)
def test_only_a_pinned_sharded_reduction_counts(tmp_path, edit):
    write_receipts(tmp_path, edit=edit)
    good, lines = chain.judge(tmp_path, SNAP, "5", "1")
    assert good is False
    assert any(line.startswith("macOS:") and ("sharded" in line or "pinned" in line) for line in lines)


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


# ------------------------------------------------------------------------------------------------ dry runs

CUT = "9" * 40


def pinned_routes(**kwargs):
    found = routes(**kwargs)
    found[f"compare/{TIP}...main"] = {"status": "ahead"}
    found[f"compare/{CUT}...main"] = {"status": "diverged"}
    return found


def test_a_pinned_plan_replans_the_cut_whatever_is_in_flight():
    rels = [release("0.9.54", marker=False), release("0.9.53")]
    read = reader(pinned_routes(rels=rels, prs=[pr(9, "release/v0.9.54")]))
    result = chain.plan(read, "0.9.54", auto=False, tip=TIP, pinned=True)
    assert (result["phase"], result["version"], result["base"], result["previous"]) == ("cut", "0.9.54", TIP, "0.9.53")


@pytest.mark.parametrize(
    ("version", "tip", "match"),
    [("", TIP, "needs the version"), ("0.9.54", CUT, "not a commit of main"), ("0.9.54", "abc", "full commit SHA")],
)
def test_a_pinned_plan_needs_a_version_and_a_commit_of_main(version, tip, match):
    with pytest.raises(chain.ChainError, match=match):
        chain.plan(reader(pinned_routes()), version, auto=False, tip=tip, pinned=True)


def test_a_real_plan_refuses_a_pinned_cut(capsys):
    assert chain.main(["plan", "--tip", TIP, "--version", "0.9.54", "--pinned"]) == 1
    assert "dry run only" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([run_row(), macos_run()], "Linux push 7 and macOS dispatch 9"),
        ([run_row(status="queued", conclusion=None)], "would wait"),
        ([run_row(attempt=2)], "would refuse"),
    ],
)
def test_a_dry_plan_reports_the_gate_and_never_waits(rows, expected):
    result, lines = chain.dry_plan(reader(pinned_routes(runs=rows)), "0.9.54", tip=TIP, pinned=True)
    assert result["phase"] == "cut"
    assert len(lines) == 1
    assert expected in lines[0]


def test_a_dry_plan_turns_a_refusal_into_its_phase():
    rels = [release("0.9.53", marker=False), release("0.9.52", marker=False), release("0.9.51")]
    result, _ = chain.dry_plan(reader(pinned_routes(rels=rels)), "0.9.54", tip=TIP, pinned=True)
    assert result["phase"] == "refused"
    assert "at most 2 in flight" in result["reason"]


def test_the_release_pr_of_a_version_is_the_newest_same_repo_one():
    rows = [
        pr(9, "release/v0.9.54"),
        pr(12, "release/v0.9.54", repo="someone/fork"),
        pr(11, "release/v0.9.54", title="x"),
        pr(10, "release/v0.9.54"),
    ]
    assert chain.release_pr_number(reader({"pulls": rows}), "0.9.54") == 10
    assert chain.release_pr_number(reader({"pulls": []}), "0.9.54") is None


def test_the_dry_candidate_verdict(monkeypatch):
    rows = [pr(9, "release/v0.9.54")]
    parents = {"parents": [{"sha": TIP}]}
    read = reader({"pulls": rows, "pulls/9": rows[0] | {"state": "open"}, f"commits/{HEAD}": parents})
    closed = reader({"pulls": rows, "pulls/9": rows[0] | {"state": "closed"}})
    good, lines = chain.dry_candidate(closed, "0.9.54", TIP)
    assert good is True
    assert "only while its PR is open" in lines[0]
    none = ["candidate: no release PR of v0.10.0; no candidate verdict"]
    assert chain.dry_candidate(read, "0.10.0", "") == (True, none)

    def finished(_read, number, version):
        assert (number, version) == (9, "0.9.54")
        return {"run_id": 77, "head": HEAD}

    monkeypatch.setattr(chain, "verify_candidate", finished)
    good, lines = chain.dry_candidate(read, "0.9.54", TIP)
    assert good is True
    assert "OK, run 77" in lines[0]
    good, lines = chain.dry_candidate(read, "0.9.54", CUT)
    assert good is False
    assert "not on the cut SHA" in lines[-1]

    def running(*_):
        msg = "candidate CI is running"
        raise chain.Wait(msg)

    monkeypatch.setattr(chain, "verify_candidate", running)
    assert chain.dry_candidate(read, "0.9.54", TIP)[0] is True

    def refused(*_):
        msg = "the candidate is refused: partial"
        raise chain.ChainError(msg)

    monkeypatch.setattr(chain, "verify_candidate", refused)
    good, lines = chain.dry_candidate(read, "0.9.54", TIP)
    assert good is False
    assert "REFUSED" in lines[0]


def test_the_dry_candidate_never_merges_or_writes(monkeypatch):
    monkeypatch.setattr(chain, "run", lambda *_a, **_k: pytest.fail("a dry candidate ran a command"))
    monkeypatch.setattr(chain, "post_check", lambda *_a: pytest.fail("a dry candidate posted a check"))
    monkeypatch.setattr(chain, "get", reader({"pulls": []}))
    assert chain.main(["candidate", "--dry-run", "--version", "0.9.54"]) == 0
    assert chain.main(["candidate", "--version", "0.9.54"]) == 1  # a real candidate needs its PR


def test_the_real_receipts_of_a_sharded_release_are_judged_by_the_same_rules():
    """The workflow pins the sharded acceptance by workflow_call; the verdict reads its result artifacts."""
    workflow = (ROOT / ".github/workflows/release-tail.yml").read_text()
    assert "uses: ./.github/workflows/acceptance.yml" in workflow
    acceptance = (ROOT / ".github/workflows/acceptance.yml").read_text()
    assert "workflow_call:" in acceptance
    assert "reduce.py --release" in acceptance
    for artifact in chain.RECEIPTS.values():
        assert artifact.removesuffix(artifact.rsplit("-", 1)[1]) + "${{ matrix.runner_os }}" in acceptance


# ------------------------------------------------------------------------------------------------ R3, R4, probe

TREE, PRHEAD = "7" * 40, "8" * 40
CANDIDATE_PROOF = (HEAD, TREE, "77")


@pytest.mark.parametrize(
    ("variant", "expected"),
    [
        ("exact", "publication is pending"),
        ("other-tree", "different tree"),
        ("other-parent", "did not merge as one commit"),
        ("unrelated-pr", "is not the release PR"),
        ("ambiguous", "ambiguous unpublished"),
        ("unnumbered", "ambiguous unpublished"),
    ],
)
def test_plan_refuses_a_second_cut_after_an_unpublished_merge(variant, expected):
    subject = "chore(release): v0.9.54" if variant == "unnumbered" else "chore(release): v0.9.54 (#9)"
    commits = [{"sha": TIP, "commit": {"message": subject}}]
    if variant == "ambiguous":
        commits.append({"sha": OTHER, "commit": {"message": "chore(release): v0.9.55 (#10)"}})
    data = routes()
    pull = pr(9, "fix/other", title="fix: other") if variant == "unrelated-pr" else pr(9, "release/v0.9.54")
    data.update(
        {
            f"compare/v0.9.53...{TIP}": {"total_commits": len(commits), "commits": commits},
            "pulls/9": pull
            | {
                "base": {"ref": "main", "repo": {"full_name": chain.REPOSITORY}},
                "merged": True,
                "merge_commit_sha": TIP,
            },
            f"compare/{TIP}...main": {"status": "identical"},
            f"compare/{BASE}...{HEAD}": content(),
            f"commits/{HEAD}": {"parents": [{"sha": BASE}], "commit": {"tree": {"sha": TREE}}},
            f"commits/{TIP}": {
                "parents": [{"sha": OTHER if variant == "other-parent" else BASE}],
                "commit": {"tree": {"sha": OTHER if variant == "other-tree" else TREE}},
            },
        }
    )
    for requested in ("", "0.9.54", "0.9.55"):
        result, _ = chain.dry_plan(reader(data), requested, tip=TIP, pinned=False)
        assert result["phase"] == "refused"
        assert expected in result["reason"]
        if variant == "exact":
            assert TIP in result["reason"]


def test_a_release_title_in_an_unrelated_commit_body_does_not_block_a_cut():
    data = routes(commits=["fix: keep a release note\n\nchore(release): v0.9.54 (#9)"])
    assert chain.plan(reader(data), "", auto=False, tip=TIP)["phase"] == "cut"


def test_compose_starts_at_the_wheel_asset_beside_pypi_and_the_snapshot_waits_for_both():
    workflow = (ROOT / ".github/workflows/release-tail.yml").read_text()
    publish = workflow[workflow.index("\n  publish:") : workflow.index("\n  acceptance:")]
    compose = publish.index("Compose the standard app")
    assert compose < publish.index("Publish the exact GitHub release bytes to PyPI") < publish.index("id: snapshot")
    assert "curl -fsSIL" in publish[compose : publish.index("gh workflow run compose.yml")]
    assert "pypi_wheel_digest" in (ROOT / "scripts/release_chain.py").read_text().split("def snapshot(")[1]


def test_the_token_reaches_the_probe_only_on_a_dispatched_dry_run_from_main():
    workflow = (ROOT / ".github/workflows/release-chain.yml").read_text()
    probe = workflow[workflow.index("\n  probe:") : workflow.index("\n  dry-candidate:")]
    assert "github.event_name == 'workflow_dispatch'" in probe
    assert "github.ref == 'refs/heads/main'" in probe
    assert "inputs.probe" in probe
    assert "inputs.dry_run" in probe


def test_hosted_release_writes_stay_inactive_until_queue_and_freeze_are_protected():
    workflows = ROOT / ".github/workflows"
    chain_source = (workflows / "release-chain.yml").read_text()
    mode = chain_source.split("id: mode", 1)[1].split("id: plan", 1)[0]
    assert "token probes are disabled" in mode
    assert "hosted release writes require the protected freeze and queue integration" in mode
    assert "dry=false" not in mode
    assert (workflows / "release-freeze.yml").read_text().count("if: ${{ false }}") == 2
    assert "if: ${{ false }}" in (workflows / "release-tail.yml").read_text()
    assert "--latest=false" not in (workflows / "publish.yml").read_text()


@pytest.mark.parametrize(
    ("inputs", "accepted"),
    [
        (("workflow_dispatch", False, False, "refs/heads/main"), False),
        (("schedule", False, False, "refs/heads/main"), False),
        (("workflow_dispatch", True, True, "refs/heads/main"), False),
        (("workflow_dispatch", False, False, "refs/heads/old-release-workflow"), False),
        (("workflow_dispatch", True, False, "refs/heads/main"), True),
        (("pull_request", False, False, "refs/pull/1/merge"), True),
    ],
)
def test_release_mode_never_grants_a_hosted_writer(tmp_path, inputs, accepted):
    """Execute the shipped admission step, including scheduled and stale-ref inputs."""
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/release-chain.yml").read_text())
    mode = next(step for step in workflow["jobs"]["plan"]["steps"] if step.get("id") == "mode")
    output = tmp_path / "output"
    event, dry, probe, ref = inputs
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", mode["run"]],
        env={
            "EVENT": event,
            "DRY": "true" if dry else "false",
            "PROBE": "true" if probe else "false",
            "VERSION": "0.9.56",
            "CUT_SHA": "",
            "REF": ref,
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if accepted:
        assert result.returncode == 0, result.stderr
        assert output.read_text().splitlines() == ["dry=true", "version=0.9.56"]
    else:
        assert result.returncode == 1
        assert not output.exists()


class FakeShell:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail
        self.branch = False

    def __call__(self, argv, env=None):
        self.calls.append(argv)
        if self.fail and self.fail in argv:
            msg = f"{self.fail} failed"
            raise chain.ChainError(msg)
        if argv[:3] == ["gh", "api", "user"]:
            return "owner 42\n"
        if "rev-parse" in argv:
            return PRHEAD + "\n"
        if "push" in argv:
            self.branch = "--delete" not in argv
        if argv[:3] == ["gh", "pr", "create"]:
            return f"https://github.com/{chain.REPOSITORY}/pull/77\n"
        if argv[:3] == ["gh", "pr", "close"]:
            self.branch = False
        return ""


def probe_reader(shell, *, freeze=True):
    base = {
        "commits/main": {"sha": TIP},
        "rules/branches/main": RULES,
        "pulls": [],
        f"commits/{PRHEAD}/check-runs?per_page=100&page=1": {
            "check_runs": [check(chain.FREEZE_CHECK)] if freeze else []
        },
    }

    def read(path):
        if path.startswith("git/ref/heads/"):
            return {"ref": path} if shell.branch else None
        return reader(base)(path)

    return read


def test_the_probe_proves_the_token_and_removes_what_it_made(monkeypatch):
    monkeypatch.setattr(chain, "post_check", lambda *_: None)
    shell = FakeShell()
    good, lines = chain.probe(probe_reader(shell), shell, Path(), "secret")
    assert good is True, lines
    pushed = [c for c in shell.calls if "push" in c and "--delete" not in c]
    assert pushed[0][-1] == f"HEAD:refs/heads/{chain.PROBE_BRANCH}{TIP[:7]}"
    assert any(c[:3] == ["gh", "pr", "create"] and "--draft" in c for c in shell.calls)
    assert any(c[:3] == ["gh", "pr", "close"] and "--delete-branch" in c for c in shell.calls)
    assert not any("merge" in c or "workflow" in c for c in shell.calls)  # nothing merged or dispatched
    assert any("CI OK, SonarCloud" in line for line in lines)
    assert any(line.startswith("not probed: pypi.yml") for line in lines)
    assert not chain.PROBE_BRANCH.startswith("release/")  # never a release branch, so never a candidate


def test_a_failed_probe_still_removes_its_branch(monkeypatch):
    monkeypatch.setattr(chain, "post_check", lambda *_: None)
    shell = FakeShell(fail="create")
    good, lines = chain.probe(probe_reader(shell), shell, Path(), "secret")
    assert good is False
    assert any("--delete" in c for c in shell.calls)
    assert shell.branch is False
    shell = FakeShell()
    good, lines = chain.probe(probe_reader(shell, freeze=False), shell, Path(), "secret")
    assert good is False
    assert any("did not reach" in line for line in lines)


def test_the_probe_needs_the_token(monkeypatch, capsys):
    monkeypatch.delenv("RELEASE_TOKEN", raising=False)
    assert chain.main(["probe"]) == 1
    assert "RELEASE_TOKEN" in capsys.readouterr().out


def test_the_tail_runs_apart_from_the_chain_so_two_releases_overlap():
    """N's tail and N+1's front never share a concurrency group; the chain ends at the tag and starts the tail."""
    import yaml

    chain_wf = yaml.safe_load((ROOT / ".github/workflows/release-chain.yml").read_text())
    tail_wf = yaml.safe_load((ROOT / ".github/workflows/release-tail.yml").read_text())
    assert tail_wf["concurrency"]["group"] == "release-tail-${{ inputs.version }}"
    assert tail_wf["concurrency"]["cancel-in-progress"] is False
    assert "release-tail" not in chain_wf["concurrency"]["group"]
    assert not {"acceptance", "verdict"} & set(chain_wf["jobs"])
    assert {"publish", "acceptance", "verdict"} <= set(tail_wf["jobs"])
    steps = chain_wf["jobs"]["publish"]["steps"]
    assert "gh workflow run release-tail.yml" in steps[-1]["run"]
    assert not any("pypi.yml" in str(step.get("run", "")) for step in steps)
    # the freeze lifts at the merge, before the tag and its tail
    merge_steps = chain_wf["jobs"]["merge"]["steps"]
    assert "freeze-sync" in merge_steps[-1]["run"]
