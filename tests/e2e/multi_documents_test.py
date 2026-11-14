"""Two documents connectors on at once (ADR 0161): switched on in mounted Settings, then links and search use both."""

import functools
import json
import os
import sys
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heinzel import Config, Document, DocumentHit, Module, Settings
from heinzel.heads import web
from heinzel.heads.cli import build
from tests.e2e._browser_result import check_browser, require_browser, wait_browser
from tests.e2e._mounted import MountedServer
from tests.e2e.docs_test import GuardedCommand
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, web_build

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]


class Drive:
    """A test-only drive-like connector whose documents open on docs.example.test."""

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title="Roadmap", text="drive text", url=ref)

    def search(self, query: str, limit: int) -> list[DocumentHit]:
        return [DocumentHit(ref="https://docs.example.test/d/1", title=f"Drive {query}")][:limit]


class Wiki:
    """A test-only wiki-like connector whose pages open on wiki.example.test."""

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title="Team page", text="wiki text")

    def search(self, query: str, limit: int) -> list[DocumentHit]:
        return [DocumentHit(ref="https://wiki.example.test/p/7", title=f"Wiki {query}")][:limit]


def stores() -> Module:
    module = Module("stores", "Test-only documents connectors: a drive and a wiki.")
    module.provides(
        "documents", modes={"read"}, hosts=["api.docs.example.test"], links=["docs.example.test"], name="drive"
    )(Drive)
    module.provides(
        "documents", modes={"read"}, hosts=["api.wiki.example.test"], links=["wiki.example.test"], name="wiki"
    )(Wiki)
    return module


def hz(kernel: object, *args: str) -> dict:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_two_documents_connectors_are_switched_on_and_both_serve(make_kernel, tmp_path, monkeypatch, engine):
    web_build.ensure_built()
    for name, folder in {
        "HOME": "home",
        "XDG_CONFIG_HOME": "config",
        "XDG_DATA_HOME": "xdg-data",
        "XDG_STATE_HOME": "xdg-state",
        "XDG_CACHE_HOME": "xdg-cache",
    }.items():
        monkeypatch.setenv(name, str(tmp_path / folder))
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(BROWSER_CACHE))
    settings = Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
    )
    kernel = make_kernel(builtins=True, settings_=settings, config=Config.load(tmp_path / "home"), stores=stores())

    monkeypatch.setattr(web, "serve", functools.partial(web.serve, restartable=False))
    with MountedServer(kernel, monkeypatch) as server:
        command = [
            sys.executable,
            str(ROOT / "scripts/web_build.py"),
            "exec",
            "node",
            "e2e/multi-documents.mjs",
            server.url,
            engine,
            os.environ.get("HZ_E2E_SHOTS", ""),
        ]
        guarded = GuardedCommand(command, cwd=ROOT, env=os.environ.copy())
        try:
            returncode = require_browser(wait_browser(guarded, 420))
        finally:
            guarded.stop()
        stdout, stderr = guarded.capture.text("stdout"), guarded.capture.text("stderr")
        check_browser(returncode, (stderr + "\n" + stdout).replace(server.token, "<redacted-session>"))

    saved = tomllib.loads((Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml").read_text(encoding="utf-8"))
    assert (
        saved["capabilities"]["documents"]
        == ["drive", "wiki"]
        == json.loads(stdout.strip().splitlines()[-1])["documents"]
    )

    # What a restart applies: a kernel on the saved configuration routes each link and asks both connectors.
    restarted = make_kernel(builtins=True, settings_=settings, config=Config.load(tmp_path / "home"), stores=stores())
    assert hz(restarted, "documents", "read", "https://docs.example.test/d/1")["source"] == "drive"
    assert hz(restarted, "documents", "read", "https://wiki.example.test/p/7")["source"] == "wiki"
    found = hz(restarted, "documents", "search", "plan")
    assert [(hit["source"], hit["title"]) for hit in found["hits"]] == [("drive", "Drive plan"), ("wiki", "Wiki plan")]
    assert found["failures"] == []
