"""Red team for the confidentiality hooks: plant leaks in a scratch repository and expect git to refuse them.

The repository's own hook definitions (the `confidentiality*` hooks of .pre-commit-config.yaml) run through prek with
made-up terms, so the wiring is tested, not just the script.
"""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
PREK = Path(sys.executable).parent / "prek"
TERMS = "acme\nglobex\nproject zebra\n"


def git(repo: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False, env=os.environ | env, timeout=60
    )


@pytest.fixture(scope="module")
def scratch(tmp_path_factory):
    """One scratch repository with the hooks installed, shared by the module's tests (setting up costs ~0.4 s)."""
    if not PREK.exists():
        pytest.skip("prek is not installed in this environment")
    tmp_path = tmp_path_factory.mktemp("hooks")
    terms = tmp_path / "terms"
    terms.write_text(TERMS, encoding="utf-8")
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("CONFIDENTIAL_TERMS_FILE", str(terms))
        monkeypatch.delenv("CONFIDENTIAL_TERMS", raising=False)
        for name in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
            monkeypatch.setenv(name, "dev@example.com")
        yield _create(tmp_path)


@pytest.fixture
def repo(scratch, monkeypatch):
    """The scratch repository, back on a clean main with the shared environment."""
    path, env = scratch
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    git(path, "switch", "-q", "--force", "main")
    git(path, "reset", "-q", "--hard", "initial")
    git(path, "clean", "-qfd")
    for branch in git(path, "branch", "--format=%(refname:short)").stdout.split():
        if branch != "main":
            git(path, "branch", "-q", "-D", branch)
    return path


def _create(tmp_path: Path) -> tuple[Path, dict[str, str]]:

    work = tmp_path / "work"
    (work / "scripts").mkdir(parents=True)
    shutil.copy(REPO / "scripts" / "check_confidentiality.py", work / "scripts")
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = [
        hook | {"entry": hook["entry"].replace("uv run python", shlex.quote(sys.executable))}
        for local in config["repos"]
        if local["repo"] == "local"
        for hook in local["hooks"]
        if hook["id"].startswith("confidentiality")
    ]
    config = {key: value for key, value in config.items() if key != "repos"} | {
        "repos": [{"repo": "local", "hooks": hooks}]
    }
    (work / ".pre-commit-config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (work / "README.md").write_text("Hello.\n", encoding="utf-8")
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.name", "Red Team")
    subprocess.run([PREK, "install", "-q"], cwd=work, check=True, capture_output=True, timeout=60)
    git(work, "add", "-A")
    assert git(work, "commit", "-q", "-m", "chore: start").returncode == 0
    git(work, "tag", "initial")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(work, "remote", "add", "origin", str(remote))
    names = ("CONFIDENTIAL_TERMS_FILE", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL")
    return work, {name: os.environ[name] for name in names}


def commits(repo: Path, files: dict[str, bytes], message: str = "docs: harmless", **env: str) -> bool:
    for name, content in files.items():
        (repo / name).write_bytes(content)
    git(repo, "add", "-A")
    done = git(repo, "commit", "-q", "-m", message, **env).returncode == 0
    git(repo, "reset", "-q", "--hard", "HEAD" if not done else "HEAD~1")
    git(repo, "clean", "-qfd")
    return done


@pytest.mark.parametrize(
    ("files", "message", "env"),
    [
        ({"README.md": b"We use Acme.\n"}, "docs: harmless", {}),
        ({"app.py": b"globex_client = 1\n"}, "docs: harmless", {}),
        ({"README.md": b"see globexdb\n"}, "docs: harmless", {}),
        ({"README.md": b"the Project\nZebra team\n"}, "docs: harmless", {}),
        ({"README.md": "glo\u200bbex\n".encode()}, "docs: harmless", {}),
        ({"logo.png": b"\x89PNG\0\0tEXt\0made by Acme\0"}, "docs: harmless", {}),
        ({"acme-notes.md": b"nothing\n"}, "docs: harmless", {}),
        ({"README.md": b"ok\n"}, "feat: support Project Zebra", {}),
        ({"README.md": b"ok\n"}, "docs: harmless", {"GIT_AUTHOR_EMAIL": "dev@acme.example"}),
    ],
    ids=[
        "text",
        "identifier",
        "glued",
        "phrase-across-lines",
        "zero-width",
        "binary",
        "file-name",
        "message",
        "author",
    ],
)
def test_leaks_are_refused(repo, files, message, env):
    assert not commits(repo, files, message, **env)


def test_clean_commits_and_ordinary_words_pass(repo):
    assert commits(repo, {"README.md": b"A small macmenu with a zebra.\n"})


def test_branch_names_are_checked_on_push(repo):
    git(repo, "switch", "-q", "-c", "feat/acme-support")
    assert git(repo, "push", "-q", "origin", "HEAD").returncode != 0
    git(repo, "switch", "-q", "-c", "feat/innocent")
    assert git(repo, "push", "-q", "origin", "HEAD").returncode == 0
