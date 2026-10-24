"""`hz team` through the CLI head with real git against local bare repositories and a fake `gh`.

GitHub is a directory: git's `insteadOf` maps https://github.com/ to local bare repositories, and `gh` is a script
on PATH that records its arguments, answers who is signed in, lists a GitHub team and "opens" pull requests. Nothing
reaches the network.
"""

import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heinzel.heads.cli import build
from heinzel.kernel import boot, user_config_dir
from heinzel.modules.team import TeamService
from heinzel.modules.workflow import WorkflowService

pytestmark = pytest.mark.integration

URL = "https://github.com/acme-example/hz-team-payments.git"
TOKEN = "ghp_exampleexampleexampleexample0000"
FAKE_GH = """\
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if args[:2] == ["api", "user"]:
    login = os.environ.get("FAKE_GH_LOGIN")
    if not login:
        sys.exit("not signed in")
    print(login)
elif args[:2] == ["api", "--paginate"]:
    print("\\n".join(os.environ["FAKE_GH_TEAM"].split(",")))
elif args[:2] == ["repo", "create"]:
    bare = pathlib.Path(os.environ["FAKE_GH_HOME"]) / (args[2] + ".git")
    bare.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(bare)], check=True, timeout=30)
    source = args[args.index("--source") + 1]
    url = "https://github.com/" + args[2] + ".git"
    subprocess.run(["git", "-C", source, "remote", "add", "origin", url], check=True, timeout=30)
elif args[:2] == ["pr", "create"]:
    print("https://github.com/acme-example/hz-team-payments/pull/7")
else:
    sys.exit("fake gh: unexpected " + " ".join(args))
"""


@pytest.fixture
def github(tmp_path, monkeypatch) -> Path:
    """GitHub as a directory of bare repositories, git mapped onto it, and a fake `gh` on PATH."""
    home = tmp_path / "github"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "fake_gh.py").write_text(FAKE_GH)
    gh = bin_dir / "gh"
    # A shell wrapper rather than a shebang naming Python: interpreter paths can hold spaces or be too long for one.
    gh.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(bin_dir / "fake_gh.py"))} "$@"\n')
    gh.chmod(0o755)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(
        f'[url "{home.as_uri()}/"]\n\tinsteadOf = https://github.com/\n[protocol "file"]\n\tallow = always\n'
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test Person")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "person@example.invalid")
    monkeypatch.setenv("FAKE_GH_HOME", str(home))
    monkeypatch.setenv("FAKE_GH_LOG", str(tmp_path / "gh.log"))
    monkeypatch.setenv("FAKE_GH_TEAM", "mira-example,Theo-Example")
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    monkeypatch.setenv("HZ_CREDENTIAL_GITHUB_TOKEN", TOKEN)
    monkeypatch.chdir(tmp_path)
    return home


def person(monkeypatch, tmp_path: Path, login: str | None) -> None:
    """Switch to another person's machine: their own configuration, data and `gh` login."""
    root = tmp_path / (login or "nobody")
    for name in ("config", "data"):
        (root / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HZ_CONFIG_HOME", str(root / "config"))
    monkeypatch.setenv("HZ_DATA_DIR", str(root / "data"))
    if login:
        monkeypatch.setenv("FAKE_GH_LOGIN", login)
    else:
        monkeypatch.delenv("FAKE_GH_LOGIN", raising=False)


def hz(*args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(build(boot()), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, {"output": result.output}


def gh_calls(tmp_path: Path) -> list[list[str]]:
    log = tmp_path / "gh.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def user_config() -> dict:
    path = user_config_dir() / "config.toml"
    return tomllib.loads(path.read_text()) if path.exists() else {}


def team_config() -> dict:
    return user_config().get("team", {})


def bare(github: Path) -> Path:
    return github / "acme-example" / "hz-team-payments.git"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, timeout=30).stdout


def bare_file(github: Path, path: str, ref: str = "main") -> str:
    return git("-C", str(bare(github)), "show", f"{ref}:{path}")


@pytest.fixture
def created(github, tmp_path, monkeypatch) -> Path:
    """Mira's team payments on 'GitHub', its roster drafted from a GitHub team."""
    person(monkeypatch, tmp_path, "mira-example")
    roster = ("--roster", "github:acme-example/payments")
    code, plan = hz("team", "new", "payments", "--github", "acme-example/hz-team-payments", *roster, "--yes")
    assert code == 0, plan
    return github


def test_new_without_yes_only_shows_the_steps_and_creates_nothing(github, tmp_path, monkeypatch):
    person(monkeypatch, tmp_path, "mira-example")
    code, plan = hz("team", "new", "payments", "--github", "acme-example/hz-team-payments")
    assert code == 0
    assert not plan["done"]
    assert [a["what"] for a in plan["actions"] if a["remote"]] == [
        "create the private repository acme-example/hz-team-payments on GitHub",
        "push main to it",
    ]
    assert not Path(plan["checkout"]).exists()
    assert not (github / "acme-example").exists()
    assert team_config() == {}
    assert plan["next"][0].endswith("--yes")


def test_new_creates_the_repository_with_the_creator_as_owner_and_a_drafted_roster(created, tmp_path):
    roster = bare_file(created, "team/roster.yaml")
    assert "handle: mira-example" in roster
    assert "owner: true" in roster
    assert "handle: theo-example" in roster
    assert "/team/ @mira-example" in bare_file(created, "CODEOWNERS")
    assert "name: payments" in bare_file(created, "hz.yaml")
    assert "hz workflow check --path workflows" in bare_file(created, ".github/workflows/hz-team.yml")
    assert team_config() == {"checkout": team_config()["checkout"], "repository": URL}
    assert ["api", "--paginate", "orgs/acme-example/teams/payments/members", "--jq", ".[].login"] in gh_calls(tmp_path)


def test_a_rostered_person_joins_by_the_url_alone_without_any_pull_request(created, tmp_path, monkeypatch):
    person(monkeypatch, tmp_path, "theo-example")
    code, plan = hz("team", "join", URL, "--yes")
    assert code == 0, plan
    assert plan["done"]
    assert plan["member"]["handle"] == "theo-example"
    assert team_config()["repository"] == URL
    assert Path(team_config()["checkout"], "hz.yaml").is_file()
    assert not [call for call in gh_calls(tmp_path) if call[:2] == ["pr", "create"]]


def test_someone_not_on_the_roster_can_read_and_proposes_to_be_added(created, tmp_path, monkeypatch):
    person(monkeypatch, tmp_path, "jonas-example")
    code, plan = hz("team", "join", URL, "--yes")
    assert code == 0
    assert plan["member"] is None
    assert "--propose" in plan["next"][0]
    code, plan = hz("team", "join", URL, "--propose", "--role", "product-manager")
    assert code == 0
    assert not plan["done"]
    assert [a["what"] for a in plan["actions"] if a["remote"]] == [
        "push hz/join-jonas-example",
        "open a pull request on acme-example/hz-team-payments",
    ]
    code, plan = hz("team", "join", URL, "--propose", "--role", "product-manager", "--yes")
    assert code == 0, plan
    assert plan["pull_request"] == "https://github.com/acme-example/hz-team-payments/pull/7"
    assert "handle: jonas-example" in bare_file(created, "team/roster.yaml", "hz/join-jonas-example")
    assert "handle: jonas-example" not in bare_file(created, "team/roster.yaml")
    pr = next(call for call in gh_calls(tmp_path) if call[:2] == ["pr", "create"])
    assert pr[pr.index("--repo") + 1] == "acme-example/hz-team-payments"
    checkout = Path(team_config()["checkout"])
    assert git("-C", str(checkout), "branch", "--show-current").strip() == "main"
    assert "jonas-example" not in (checkout / "team" / "roster.yaml").read_text()


def test_an_owner_adds_someone_by_a_pull_request(created, tmp_path, monkeypatch):
    code, plan = hz("team", "add", "priya", "--login", "priya-example", "--role", "principal", "--yes")
    assert code == 0, plan
    assert plan["pull_request"]
    assert "handle: priya" in bare_file(created, "team/roster.yaml", "hz/add-priya")


def test_no_name_email_or_token_reaches_any_branch_of_the_team_repository(created, tmp_path, monkeypatch):
    assert hz("team", "add", "priya", "--login", "priya-example", "--yes")[0] == 0
    person(monkeypatch, tmp_path, "jonas-example")
    assert hz("team", "join", URL, "--propose", "--yes")[0] == 0
    for ref in ("main", "hz/add-priya", "hz/join-jonas-example"):
        files = git("-C", str(bare(created)), "ls-tree", "-r", "--name-only", ref).split()
        text = "".join(bare_file(created, path, ref) for path in files)
        assert "person@example.invalid" not in text
        assert "Test Person" not in text
        assert TOKEN not in text


def test_a_member_who_is_not_an_owner_cannot_add_anyone(created, tmp_path, monkeypatch):
    person(monkeypatch, tmp_path, "theo-example")
    assert hz("team", "join", URL, "--yes")[0] == 0
    code, out = hz("team", "add", "priya", "--login", "priya-example")
    assert code != 0
    assert "Only owners add members" in out["output"]


def test_show_lists_the_team_and_its_members(created):
    code, shown = hz("team", "show")
    assert code == 0
    assert shown["name"] == "payments"
    assert [m["handle"] for m in shown["members"]] == ["mira-example", "theo-example"]
    assert shown["members"][0]["owner"]


def test_a_hostile_team_repository_schedules_installs_and_binds_nothing(created, tmp_path, monkeypatch):
    checkout = Path(team_config()["checkout"])
    contract = Path(__file__).parents[1] / "fixtures" / "workflows" / "weekly-digest.prose.md"
    (checkout / "workflows" / "weekly-digest.prose.md").write_text(contract.read_text())
    (checkout / "hz.toml").write_text(
        '[capabilities]\ntracker = "evil"\n[workflow]\nschedule_team = ["weekly-digest"]\n'
    )
    git("-C", str(checkout), "add", "--all")
    git("-C", str(checkout), "commit", "--quiet", "-m", "Schedule a contract on every daemon")
    git("-C", str(checkout), "push", "--quiet", "origin", "main")
    person(monkeypatch, tmp_path, "theo-example")
    before = len(gh_calls(tmp_path))
    assert hz("team", "join", URL, "--yes")[0] == 0
    assert gh_calls(tmp_path)[before:] == [["api", "user", "--jq", ".login"]]
    assert set(user_config()) == {"team"}
    code, listed = hz("workflow", "list")
    assert code == 0
    [team_contract] = [c for c in listed["contracts"] if c["namespace"] == "team"]
    assert not team_contract["scheduled"]


@pytest.mark.parametrize(
    ("url", "reason"),
    [("ext::sh -c touch% {marker}", "spaces"), ("ext::touch", "transport"), ("--upload-pack=touch", "dash")],
)
def test_a_url_git_would_run_as_a_command_is_refused_before_anything_runs(github, tmp_path, monkeypatch, url, reason):
    person(monkeypatch, tmp_path, "theo-example")
    marker = tmp_path / "pwned"
    result = CliRunner().invoke(build(boot()), ["team", "join", "--json", "--", url.format(marker=marker)])
    assert result.exit_code != 0
    assert reason in " ".join(result.output.split())
    assert not marker.exists()
    assert not (tmp_path / "theo-example" / "data" / "team").exists()
    assert gh_calls(tmp_path) == []


def test_workflow_finds_the_team_contracts_in_the_joined_checkout(created, tmp_path, monkeypatch):
    person(monkeypatch, tmp_path, "theo-example")
    assert hz("team", "join", URL, "--yes")[0] == 0
    code, listed = hz("workflow", "list")
    assert code == 0
    team = next(d for d in listed["directories"] if d["namespace"] == "team")
    assert team == {"namespace": "team", "path": str(Path(team_config()["checkout"]) / "workflows"), "exists": True}


def test_a_team_made_yours_counts_at_once_in_the_same_process(github, tmp_path, monkeypatch):
    person(monkeypatch, tmp_path, "mira-example")
    checkout = tmp_path / "legacy"
    git("init", "--quiet", str(checkout))
    kernel = boot()
    kernel.resolve(TeamService).adopt(checkout)
    assert kernel.resolve(WorkflowService).directories["team"] == checkout.resolve() / "workflows"
