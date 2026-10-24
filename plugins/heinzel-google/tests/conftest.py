"""Shared fixtures: a fake Google Workspace CLI behind the plugin's `Gws`, and kernels that use it. Nothing goes online.

heinzel.testing (a pytest plugin, loaded automatically) brings the rest: every test is exactly one of unit,
integration or e2e and gets its level's timeout (docs/CODING_STANDARDS.md#tests), and every test has its own
HZ_CONFIG_HOME, HZ_DATA_DIR, HZ_CACHE_DIR, HZ_STATE_DIR, HZ_LOG_DIR and daemon unit directory, without the developer's
HZ_* variables.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from heinzel_google import GoogleSession, GoogleSettings, Gws, Process

from heinzel import Config, Kernel, Module, Settings
from heinzel.kernel import ModuleRecord, Origin, discover

DOC_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz"
TEST_ORIGIN = Origin(distribution="heinzel-test", version="0", builtin=False)
SIGNED_IN = {"auth_method": "oauth2", "credential_source": "encrypted_credentials", "storage": "keyring"}


@dataclass
class Run:
    """One `gws` run the fake received."""

    executable: str
    arguments: list[str]
    timeout: float

    @property
    def command(self) -> tuple[str, ...]:
        return (
            tuple(self.arguments[: self.arguments.index("--params")])
            if "--params" in self.arguments
            else tuple(self.arguments)
        )

    @property
    def params(self) -> dict[str, Any]:
        return json.loads(self.arguments[self.arguments.index("--params") + 1]) if "--params" in self.arguments else {}


def output(body: object, exit_code: int = 0, stderr: bytes = b"") -> Process:
    """What `gws` prints: `body` as JSON on stdout."""
    return Process(exit_code, json.dumps(body).encode(), stderr)


@dataclass
class FakeGws(Gws):
    """The Google Workspace CLI in memory: answers per command (and page token), records runs and waits."""

    routes: dict[tuple[tuple[str, ...], str | None], list[Process]] = field(default_factory=dict)
    runs: list[Run] = field(default_factory=list)
    slept: list[float] = field(default_factory=list)
    status: Process = field(default_factory=lambda: output(SIGNED_IN))
    error: OSError | None = None
    fail_after: int = 0
    """Runs that succeed before `error` is raised."""
    login_code: int | None = 0
    version: Process = field(default_factory=lambda: Process(0, b"gws 0.22.5\n"))
    logins: list[list[str]] = field(default_factory=list)

    def run(self, executable: str, arguments: Sequence[str], timeout: float) -> Process:
        run = Run(executable, list(arguments), timeout)
        self.runs.append(run)
        if self.error is not None and len(self.runs) > self.fail_after:
            raise self.error
        if run.command == ("auth", "status"):
            return self.status
        if run.command == ("--version",):
            return self.version
        queue = self.routes.get((run.command, run.params.get("pageToken")))
        assert queue, f"unexpected gws run: {arguments}"
        return queue.pop(0) if len(queue) > 1 else queue[0]  # the last answer repeats

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def login(self, executable: str, arguments: Sequence[str], timeout: float) -> int | None:
        self.logins.append(list(arguments))
        if self.login_code == 0:
            self.status = output(SIGNED_IN)
        return self.login_code

    def on(self, command: tuple[str, ...], *answers: Process, page: str | None = None) -> None:
        """Answer runs of `command` (with page token `page`) with `answers` in turn, the last one from then on."""
        self.routes[command, page] = list(answers)

    def json(self, command: tuple[str, ...], body: object, page: str | None = None) -> None:
        self.on(command, output(body), page=page)

    def file(self, file_id: str, mime: str, name: str = "Plan", version: str = "42") -> None:
        """Drive metadata of a file (the fake answers every `files get` alike)."""
        self.json(("drive", "files", "get"), {"id": file_id, "name": name, "mimeType": mime, "version": version})

    def doc(self, file_id: str = DOC_ID, title: str = "Plan", content: list[object] | None = None) -> None:
        """A Google Doc: Drive metadata and the Docs API's document with one tab."""
        self.file(file_id, "application/vnd.google-apps.document", title)
        body = {
            "content": content
            if content is not None
            else [{"paragraph": {"elements": [{"textRun": {"content": "Hello\n"}}]}}]
        }
        self.json(
            ("docs", "documents", "get"),
            {
                "documentId": file_id,
                "title": title,
                "revisionId": "rev-7",
                "tabs": [{"tabProperties": {"tabId": "t.0", "title": "Tab 1"}, "documentTab": {"body": body}}],
            },
        )

    @property
    def api_runs(self) -> list[Run]:
        return [r for r in self.runs if r.command not in {("auth", "status"), ("--version",)}]


@pytest.fixture
def gws_path(tmp_path: Path) -> str:
    """An executable file standing in for `gws`; the fake never starts it."""
    path = tmp_path / "bin" / "gws"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def fake_gws() -> FakeGws:
    """A fresh fake Google Workspace CLI."""
    return FakeGws()


@pytest.fixture
def session(fake_gws: FakeGws, gws_path: str) -> GoogleSession:
    """A session whose `gws` is the fake."""
    return GoogleSession(GoogleSettings(gws_path=gws_path), fake_gws)


@pytest.fixture
def boot() -> Callable[..., Kernel]:
    """Boot a kernel the way the `hz` command does: Heinzelmännchen's built-in modules plus every installed plugin."""

    def make(**settings: object) -> Kernel:
        records, errors = discover()
        return Kernel(Settings(**settings), records, errors)

    return make


@pytest.fixture
def google_kernel(fake_gws: FakeGws, gws_path: str, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Kernel]:
    """A kernel with every installed plugin, extra test-only modules, the given configuration and the fake `gws`."""
    monkeypatch.setenv("HZ_GOOGLE__GWS_PATH", gws_path)

    def make(config: Config | None = None, **modules: Module) -> Kernel:
        records, errors = discover()
        records += [ModuleRecord(qualname=name, module=module, origin=TEST_ORIGIN) for name, module in modules.items()]
        kernel = Kernel(Settings(), records, errors, config or Config.empty())
        kernel.bind(Gws, fake_gws)
        return kernel

    return make
