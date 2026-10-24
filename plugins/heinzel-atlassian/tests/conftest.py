"""Shared fixtures: a fake `acli`, in memory or as an executable on PATH, and kernels that use it. Nothing goes online.

The fake answers with the samples in `tests/fixtures/`: made-up projects and people in the shape the adapter
assumes acli prints with `--json` (Jira's REST issue objects, Confluence's v2 page). The shape is not yet verified
against a logged-in acli. The real acli never runs: the executable fixture puts only the fake's directory on PATH.

heinzel.testing (a pytest plugin, loaded automatically) brings the rest: every test is exactly one of unit,
integration or e2e and gets its level's timeout (docs/CODING_STANDARDS.md#tests), and every test has its own
HZ_CONFIG_HOME, HZ_DATA_DIR, HZ_CACHE_DIR, HZ_STATE_DIR, HZ_LOG_DIR and daemon unit directory, without the developer's
HZ_* variables.
"""

import json
import os
import stat
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pytest
from heinzel_atlassian import Acli, Completed

from heinzel import Config, Kernel, Module, Settings
from heinzel.kernel import ModuleRecord, Origin, discover

SITE = "https://acme.atlassian.net"
FIXTURES = Path(__file__).parent / "fixtures"
SEARCH = "jira workitem search"
PAGE = "confluence page view"
UNAUTHORIZED = "✗ Error: unauthorized: use 'acli jira auth login' to authenticate\n"
VERSION = "acli version 1.3.39-stable\n"
TEST_ORIGIN = Origin(distribution="heinzel-test", version="0", builtin=False)

SCRIPT = """#!{python}
import json, os, sys, time
args = sys.argv[1:]
with open({log!r}, "a", encoding="utf-8") as log:
    seen = sorted(k for k in os.environ if k.startswith("HZ_CREDENTIAL_"))
    record = {{"args": args, "credentials": seen, "no_color": os.environ.get("NO_COLOR"), "cwd": os.getcwd()}}
    record["environment"] = sorted(os.environ)
    record["stdin"] = sys.stdin.read() if sys.stdin is not None else None
    log.write(json.dumps(record) + "\\n")
with open({spec!r}, encoding="utf-8") as spec:
    answer = json.load(spec).get(" ".join(args[:3]))
if answer is None:
    sys.stderr.write("✗ Error: unknown command " + " ".join(args[:3]) + "\\n")
    sys.exit(2)
if answer["linger"]:  # a child that inherits the pipes and outlives acli; "escape": in a session of its own
    import subprocess
    escape = answer["linger"] == "escape"
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=escape)
time.sleep(answer["sleep"])
for _ in range(answer["repeat"]):
    sys.stdout.write(answer["stdout"])
sys.stdout.flush()
sys.stderr.write(answer["stderr"])
sys.exit(answer["exit"])
"""


def sample(name: str) -> Any:
    """A sample answer from tests/fixtures, in the shape the adapter assumes."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@dataclass
class Call:
    """One acli run the fake saw."""

    args: list[str]
    timeout: float | None = None
    credentials: list[str] = field(default_factory=list)
    no_color: str | None = None
    stdin: str | None = None
    cwd: str | None = None
    environment: list[str] = field(default_factory=list)


@dataclass
class FakeAcli(Acli):
    """acli's answers per subcommand (`jira workitem search`, `confluence page view`); records every run.

    In memory it replaces `Acli` through the kernel; `install` also writes an executable `acli` that answers the same.
    """

    SEARCH = SEARCH
    PAGE = PAGE
    UNAUTHORIZED = UNAUTHORIZED
    sample = staticmethod(sample)

    answers: dict[str, dict[str, Any]] = field(default_factory=dict)
    memory_calls: list[Call] = field(default_factory=list)
    error: Exception | None = None
    location: str | None = "/opt/acli/bin/acli"
    reported: tuple[int, int, int] | None = (1, 3, 39)
    version_error: Exception | None = None
    spec: Path | None = None
    log: Path | None = None

    def path(self) -> str | None:
        return self.location

    def version(self) -> tuple[int, int, int] | None:
        if self.version_error is not None:
            raise self.version_error
        return self.reported

    def run(self, args: Sequence[str], timeout: float) -> Completed:
        self.memory_calls.append(Call(list(args), timeout))
        if self.error is not None:
            raise self.error
        answer = self.answers.get(" ".join(args[:3]))
        assert answer is not None, f"unexpected acli {' '.join(args)}"
        stdout = (answer["stdout"] * answer["repeat"]).encode()
        return Completed(answer["exit"], stdout, answer["stderr"].encode(), overflow=False)

    @property
    def calls(self) -> list[Call]:
        """The runs, in memory and of the executable, but `acli --version`."""
        if self.log is None or not self.log.exists():
            return self.memory_calls
        lines = self.log.read_text(encoding="utf-8").splitlines()
        logged = [Call(**json.loads(line)) for line in lines]
        return self.memory_calls + [call for call in logged if call.args != ["--version"]]

    def answer(  # noqa: PLR0913 - what tests vary
        self,
        command: str,
        stdout: object = "",
        *,
        stderr: str = "",
        exit_status: int = 0,
        sleep: float = 0.0,
        repeat: int = 1,
        linger: bool | str = False,
    ) -> None:
        """Answer `acli <command> ...` with `stdout` (JSON unless a string), `stderr` and an exit status."""
        text = stdout if isinstance(stdout, str) else json.dumps(stdout)
        self.answers[command] = {
            "stdout": text,
            "stderr": stderr,
            "exit": exit_status,
            "sleep": sleep,
            "repeat": repeat,
            "linger": linger,
        }
        self._save()

    def search(self, issues: object) -> None:
        """Answer Jira searches with `issues`."""
        self.answer(SEARCH, issues)

    def page(self, page_id: str, title: str = "Plan", storage: str = "<p>Hello</p>", version: int = 7) -> None:
        """Answer a Confluence page view, shaped like the fixture."""
        page = sample("confluence_page_view.json")
        page.update(id=page_id, title=title)
        page["version"]["number"] = version
        page["body"]["storage"]["value"] = storage
        page["_links"]["webui"] = f"/spaces/WID/pages/{page_id}/{quote_plus(title)}"
        self.answer(PAGE, page)

    def fail(self, command: str, stderr: str, exit_status: int = 1) -> None:
        """Fail `acli <command>` with `stderr`."""
        self.answer(command, stderr=stderr, exit_status=exit_status)

    def install(self, directory: Path) -> Path:
        """Write the executable `acli` into `directory`; it answers from a spec file this fake keeps current."""
        self.spec, self.log = directory / "spec.json", directory / "log.jsonl"
        self._save()
        script = directory / "acli"
        script.write_text(
            SCRIPT.format(python=sys.executable, spec=str(self.spec), log=str(self.log)), encoding="utf-8"
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return directory

    def _save(self) -> None:
        if self.spec is not None:
            version = {"stdout": VERSION, "stderr": "", "exit": 0, "sleep": 0.0, "repeat": 1, "linger": False}
            self.spec.write_text(json.dumps({"--version": version} | self.answers), encoding="utf-8")

    @staticmethod
    def issue(  # noqa: PLR0913 - the fields tests vary
        number: int,
        *,
        updated: str,
        status: str = "To Do",
        assignee: str | None = None,
        labels: tuple[str, ...] = (),
        project: str = "WID",
    ) -> dict[str, Any]:
        """An issue as acli prints it, shaped like the fixture."""
        issue = sample("jira_workitem_search.json")[0]
        issue.update(id=str(10000 + number), key=f"{project}-{number}")
        issue["fields"] = {
            "summary": f"Issue {number}",
            "status": {"name": status, "statusCategory": {"key": "new"}},
            "updated": updated,
            "assignee": {"accountId": "5b10ac8d82e05b22cc7d4ef5", "displayName": assignee} if assignee else None,
            "labels": list(labels),
        }
        return issue


@pytest.fixture
def fake_acli() -> FakeAcli:
    """A fresh fake acli, in memory."""
    return FakeAcli()


@pytest.fixture
def acli_on_path(fake_acli: FakeAcli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeAcli:
    """The fake installed as the only `acli` on PATH (the real one is out of reach), answering as `fake_acli`."""
    directory = tmp_path / "bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", str(fake_acli.install(directory)))
    return fake_acli


@pytest.fixture
def no_acli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No `acli` on PATH."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert os.environ["PATH"] == str(empty)


@pytest.fixture
def boot() -> Callable[..., Kernel]:
    """Boot a kernel the way the `hz` command does: Heinzelmännchen's built-in modules plus every installed plugin."""

    def make(**settings: object) -> Kernel:
        records, errors = discover()
        return Kernel(Settings(**settings), records, errors)

    return make


@pytest.fixture
def atlassian_kernel() -> Callable[..., Kernel]:
    """A kernel with every installed plugin, extra test-only modules and the given configuration; acli is real."""

    def make(config: Config | None = None, **modules: Module) -> Kernel:
        records, errors = discover()
        records += [ModuleRecord(qualname=name, module=module, origin=TEST_ORIGIN) for name, module in modules.items()]
        return Kernel(Settings(), records, errors, config or Config.empty())

    return make
