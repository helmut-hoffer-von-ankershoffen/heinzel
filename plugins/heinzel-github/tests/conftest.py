"""Shared fixtures: a fake GitHub CLI, in memory or as a `gh` executable on `PATH`, and kernels. Nothing goes online.

Unit tests use `FakeGh`, which replaces the plugin's `Gh` runner in memory. Integration tests use `fake_gh_cli`: the
real runner starts `tests/fake_gh.py` as `gh`, the only `gh` on a `PATH` of its own, which answers from routes and
the recorded JSON in `tests/fixtures` (made-up repositories and people).

heinzel.testing (a pytest plugin, loaded automatically) brings the rest: every test is exactly one of unit,
integration or e2e and gets its level's timeout (docs/CODING_STANDARDS.md#tests), and every test has its own
HZ_CONFIG_HOME, HZ_DATA_DIR, HZ_CACHE_DIR, HZ_STATE_DIR, HZ_LOG_DIR and daemon unit directory, without the developer's
HZ_* variables.
"""

import json
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from heinzel_github import Gh, GhResult

from heinzel import Config, HeinzelError, Kernel, Module, Settings
from heinzel.kernel import ModuleRecord, Origin, discover

TESTS = Path(__file__).parent
FIXTURES = TESTS / "fixtures"
TEST_ORIGIN = Origin(distribution="heinzel-test", version="0", builtin=False)


def fixture(name: str) -> list[dict[str, Any]]:
    """A recorded answer from `tests/fixtures`."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@dataclass
class Call:
    """A `gh api` call the in-memory fake received."""

    args: list[str]
    host: str
    timeout: float

    @property
    def path(self) -> str:
        return next(arg for arg in self.args if arg.startswith("repos/"))

    @property
    def fields(self) -> dict[str, str]:
        pairs = (self.args[i + 1].split("=", 1) for i, arg in enumerate(self.args) if arg == "--raw-field")
        return dict(pairs)


@dataclass
class FakeGh(Gh):
    """The `gh` runner in memory: serves pages per path, says its version and login, and records every call."""

    routes: dict[str, list[GhResult]] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    installed: bool = True
    logged: bool = True
    found: tuple[int, int, int] | None = (2, 103, 2)
    broken: str | None = None
    """A message `version` fails with, as for a gh that can't start."""

    def executable(self) -> str | None:
        return "/usr/local/bin/gh" if self.installed else None

    def version(self) -> tuple[int, int, int] | None:
        if self.broken:
            raise HeinzelError(self.broken)
        return self.found

    def run(self, args: list[str], *, host: str, timeout: float) -> GhResult:
        call = Call(list(args), host, timeout)
        self.calls.append(call)
        pages = self.routes.get(call.path)
        assert pages is not None, f"unexpected gh call {args}"
        page = int(call.fields.get("page", "1"))
        return pages[page - 1] if page <= len(pages) else GhResult(0, b"[]", "")

    def logged_in(self) -> bool:
        return self.logged

    def serve(self, path: str, *pages: list[dict[str, Any]]) -> None:
        """Answer `gh api path` with `pages`, one per page number, then empty pages."""
        self.routes[path] = [GhResult(0, json.dumps(items).encode(), "") for items in pages]

    def fail(self, path: str, returncode: int | None, stderr: str = "", body: object = None) -> None:
        """Answer `gh api path` as a failing `gh` does: its exit code, stderr, and GitHub's error body on stdout."""
        stdout = json.dumps(body).encode() if body is not None else b""
        self.routes[path] = [GhResult(returncode, stdout, stderr)]

    @staticmethod
    def pull(  # noqa: PLR0913 - the fields tests vary
        number: int,
        *,
        updated_at: str,
        merged_at: str | None,
        title: str = "",
        user: str | None = "ada",
        labels: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """A pull request shaped like `pulls.json`, with the fields tests vary."""
        item = dict(fixture("pulls.json")[0])
        item.update(
            number=number,
            title=title or f"Pull {number}",
            html_url=f"https://github.com/acme/widgets/pull/{number}",
            user={"login": user, "id": 1} if user else None,
            labels=[{"id": i, "name": name, "color": "ededed"} for i, name in enumerate(labels)],
            updated_at=updated_at,
            closed_at=merged_at or updated_at,
            merged_at=merged_at,
        )
        return item

    @staticmethod
    def issue(  # noqa: PLR0913 - the fields tests vary
        number: int,
        *,
        updated_at: str,
        state: str = "open",
        assignee: str | None = None,
        labels: tuple[str, ...] = (),
        pull_request: bool = False,
    ) -> dict[str, Any]:
        """An issue shaped like `issues.json`; with `pull_request`, the pull request GitHub mixes in."""
        item = dict(fixture("issues.json")[0])
        item.update(
            number=number,
            title=f"Issue {number}",
            html_url=f"https://github.com/acme/widgets/issues/{number}",
            state=state,
            assignee={"login": assignee} if assignee else None,
            labels=[{"name": name} for name in labels],
            updated_at=updated_at,
        )
        if pull_request:
            item["pull_request"] = {"url": f"https://api.github.com/repos/acme/widgets/pulls/{number}"}
        return item


@pytest.fixture
def fake_gh() -> FakeGh:
    """A fresh in-memory `gh`."""
    return FakeGh()


TOKEN_VARIABLES = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")


@dataclass
class FakeGhCli:
    """The fake `gh` executable's routes, gh's configuration directory, and the calls it recorded."""

    directory: Path
    routes: dict[str, Any] = field(default_factory=dict)

    @property
    def bin(self) -> Path:
        return self.directory / "bin"

    @property
    def config(self) -> Path:
        """gh's configuration directory (`GH_CONFIG_DIR`), with its hosts file."""
        return self.directory / "config"

    def _save(self) -> None:
        (self.directory / "routes.json").write_text(json.dumps(self.routes), encoding="utf-8")

    def answer(self, path: str, page: int = 1, **answer: object) -> None:
        """How `gh api path` answers for `page`: `fixture` or `stdout`, `stderr`, `exit`, `sleep`, `pad`."""
        self.routes["api"][f"{path}?page={page}"] = answer
        self._save()

    def version(self, output: str, sleep: float = 0) -> None:
        """What `gh --version` prints, after `sleep` seconds."""
        self.routes["version"] = {"stdout": output, "sleep": sleep}
        self._save()

    def login(self, *hosts: str) -> None:
        """The hosts gh's hosts file has a login for, as gh writes it."""
        self.config.mkdir(parents=True, exist_ok=True)
        text = "".join(f"{host}:\n    git_protocol: https\n    user: ada\n" for host in hosts)
        (self.config / "hosts.yml").write_text(text, encoding="utf-8")

    def calls(self) -> list[dict[str, Any]]:
        """Every call so far: `argv`, `env` (all variables gh saw) and `cwd`."""
        log = self.directory / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def api_calls(self) -> list[dict[str, Any]]:
        """The `gh api` calls, without `gh --version`."""
        return [call for call in self.calls() if call["argv"][:1] == ["api"]]


def install_gh(directory: Path, shebang: str = sys.executable) -> Path:
    """Write `tests/fake_gh.py` as an executable `gh` in `directory`/bin, started by `shebang`.

    Its routes file and call log are in `directory`, written into the script: gh's environment is an allowlist, so a
    variable could not tell the fake where they are.
    """
    executable = directory / "bin" / "gh"
    executable.parent.mkdir(parents=True, exist_ok=True)
    source = (TESTS / "fake_gh.py").read_text(encoding="utf-8")
    header = f"#!{shebang}\nROUTES = {str(directory / 'routes.json')!r}\nLOG = {str(directory / 'calls.jsonl')!r}\n"
    executable.write_text(header + source, encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


@pytest.fixture(name="install_gh")
def install_gh_fixture() -> Callable[..., Path]:
    """`install_gh`, for tests that need a `gh` of their own."""
    return install_gh


@pytest.fixture
def no_gh_login(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No token variable gh reads, and an empty gh configuration directory (`GH_CONFIG_DIR`), returned."""
    for name in TOKEN_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / "gh-config"
    monkeypatch.setenv("GH_CONFIG_DIR", str(config))
    return config


@pytest.fixture
def fake_gh_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_gh_login: Path) -> FakeGhCli:
    """A fake `gh` 2.103.2 as the only program on `PATH`, logged in to github.com in its hosts file, no routes yet."""
    del no_gh_login  # wanted for the environment it leaves; the hosts file goes elsewhere
    cli = FakeGhCli(tmp_path / "gh", {"fixtures": str(FIXTURES), "api": {}})
    install_gh(cli.directory)
    cli._save()
    monkeypatch.setenv("PATH", str(cli.bin))
    monkeypatch.setenv("GH_CONFIG_DIR", str(cli.config))
    cli.login("github.com")
    return cli


@pytest.fixture
def boot() -> Callable[..., Kernel]:
    """Boot a kernel the way the `hz` command does: Heinzelmännchen's built-in modules plus every installed plugin."""

    def make(**settings: object) -> Kernel:
        records, errors = discover()
        return Kernel(Settings(**settings), records, errors)

    return make


@pytest.fixture
def github_kernel() -> Callable[..., Kernel]:
    """A kernel with every installed plugin, extra test-only modules and the given configuration; the real `Gh`."""

    def make(config: Config | None = None, **modules: Module) -> Kernel:
        records, errors = discover()
        records += [ModuleRecord(qualname=name, module=module, origin=TEST_ORIGIN) for name, module in modules.items()]
        return Kernel(Settings(), records, errors, config or Config.empty())

    return make
