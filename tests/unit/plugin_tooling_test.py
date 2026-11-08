"""Plugin tooling executes commands only after verified wheel installation succeeds."""

import hashlib
import http.server
import importlib.util
import io
import subprocess
import threading
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit
SCRIPT = Path(__file__).resolve().parents[2] / "scripts/plugin_tooling.py"
spec = importlib.util.spec_from_file_location("plugin_tooling_under_test", SCRIPT)
assert spec
assert spec.loader
tooling = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tooling)
fetch_verified = tooling.fetch_verified  # the real one; the autouse fixture replaces the module's for run()


WHEEL = b"verified toolkit wheel fixture"
DIGEST = hashlib.sha256(WHEEL).hexdigest()


@pytest.fixture(autouse=True)
def offline_toolkit(monkeypatch, tmp_path_factory):
    """No test reaches GitHub or the user's cache: the pinned toolkit resolves to a local fixture wheel."""
    cache = tmp_path_factory.mktemp("cache")
    monkeypatch.setattr(tooling, "CACHE", cache)

    def fetch(url, digest):
        wheel = cache / f"{digest}.whl"
        wheel.write_bytes(WHEEL)
        return wheel

    monkeypatch.setattr(tooling, "fetch_verified", lambda url, digest, **_: fetch(url, digest))
    return cache


def test_failed_hash_locked_install_never_executes_toolkit(monkeypatch):
    calls = []
    monkeypatch.setattr(tooling.shutil, "which", lambda _: "/fictional/uv")

    def execute(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["pip", "install"]:
            assert "--require-hashes" in command
            assert "--no-build" in command
            installed = Path(command[command.index("--requirements") + 1]).read_text().splitlines()
            original = tooling.REQUIREMENTS.read_text().splitlines()
            assert [installed[0], *installed[2:]] == [original[0], *original[2:]], "other pins stay unchanged"
            name, digest = original[1].split(" @ ")[0], original[1].rsplit("--hash=", 1)[1]
            assert installed[1] != original[1]
            assert installed[1].startswith(f"{name} @ file://")
            assert installed[1].endswith("/heinzel_plugin_tools-0.2.1-py3-none-any.whl --hash=" + digest)
            raise subprocess.CalledProcessError(1, command)
        assert command[1] == "venv", "only environment creation may precede verified installation"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tooling.subprocess, "run", execute)
    with pytest.raises(subprocess.CalledProcessError):
        tooling.run("hz-plugin", ["check"])
    assert len(calls) == 2


def test_tampered_candidate_never_reaches_installation(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tooling.shutil, "which", lambda _: "/fictional/uv")
    monkeypatch.undo()
    monkeypatch.setattr(tooling, "CACHE", tmp_path)
    monkeypatch.setattr(tooling.shutil, "which", lambda _: "/fictional/uv")
    monkeypatch.setattr(tooling.time, "sleep", lambda _: None)
    monkeypatch.setattr(tooling.urllib.request, "urlopen", lambda *_, **__: io.BytesIO(b"tampered wheel"))

    def execute(command, **kwargs):
        calls.append(command)
        assert command[1] == "venv", "a tampered candidate must not be installed or executed"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tooling.subprocess, "run", execute)
    with pytest.raises(ValueError, match="integrity check"):
        tooling.run("hz-plugin", ["check"], toolkit="https://example.test/toolkit.whl#sha256=" + "a" * 64)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "candidate",
    [
        "https://example.test/toolkit.whl",
        "https://example.test/toolkit.whl#" + "a" * 64,
        "http://example.test/toolkit.whl#sha256=" + "a" * 64,
        "https://example.test/toolkit.tar.gz#sha256=" + "a" * 64,
    ],
)
def test_remote_candidates_require_https_wheels_and_digests(candidate, tmp_path, monkeypatch):
    def refuse_download(*args, **kwargs):
        pytest.fail("an invalid candidate reached the network")

    monkeypatch.setattr(tooling.urllib.request, "urlopen", refuse_download)
    with pytest.raises(ValueError, match="HTTPS wheel URL"):
        tooling.candidate_wheel(candidate, tmp_path)


def test_verified_candidate_is_the_only_artifact_returned(tmp_path, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(tooling, "CACHE", tmp_path / "cache")
    content = b"verified wheel fixture"
    digest = tooling.hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(tooling.urllib.request, "urlopen", lambda *_, **__: io.BytesIO(content))
    wheel = tooling.candidate_wheel("https://example.test/toolkit.whl#sha256=" + digest, tmp_path)
    assert wheel == tmp_path / "toolkit.whl"
    assert wheel.read_bytes() == content


def test_only_documented_toolkit_programs_can_execute(monkeypatch):
    def refuse_run(*args, **kwargs):
        pytest.fail("an unsupported executable reached a subprocess")

    monkeypatch.setattr(tooling.subprocess, "run", refuse_run)
    with pytest.raises(ValueError, match="Choose hz-plugin or python"):
        tooling.run("bash", ["-c", "exit 0"])


@pytest.mark.parametrize(
    ("program", "arguments"),
    [
        ("hz-plugin", []),
        ("hz-plugin", ["--help"]),
        ("hz-plugin", ["-c", "exit 0"]),
        ("hz-plugin", ["../check"]),
        ("python", []),
        ("python", ["-c", "print(1)"]),
        ("python", ["-m", "os"]),
        ("python", ["-m", "heinzel_plugin_tools"]),
        ("python", ["script.py"]),
        ("python", ["-m"]),
    ],
)
def test_only_toolkit_subcommands_and_modules_reach_a_subprocess(monkeypatch, program, arguments):
    def refuse_run(*args, **kwargs):
        pytest.fail("an argument outside the toolkit interface reached a subprocess")

    monkeypatch.setattr(tooling.subprocess, "run", refuse_run)
    with pytest.raises(ValueError, match="toolkit"):
        tooling.run(program, arguments)


def test_local_candidate_with_option_shaped_name_becomes_an_absolute_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    candidate = Path("--target=unexpected.whl")
    candidate.write_bytes(b"local developer wheel fixture")
    assert tooling.candidate_wheel(str(candidate), tmp_path) == candidate.resolve()


def test_toolkit_runs_with_its_environment_first_on_path(monkeypatch):
    """The toolkit finds `typos` and its interpreter in the disposable environment, as under `uv run`."""
    runs = []
    monkeypatch.setattr(tooling.shutil, "which", lambda _: "/fictional/uv")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(
        tooling.subprocess,
        "run",
        lambda command, **kwargs: runs.append((command, kwargs)) or SimpleNamespace(returncode=0),
    )
    assert tooling.run("hz-plugin", ["check"]) == 0
    command, kwargs = runs[-1]
    environment = Path(command[0]).parent.parent
    assert kwargs["env"]["PATH"] == f"{environment / 'bin'}{tooling.os.pathsep}/usr/bin"
    assert kwargs["env"]["VIRTUAL_ENV"] == str(environment)


class FakeRelease:
    """A loopback release host answering a scripted sequence of statuses, then the last one forever."""

    def __init__(self, statuses, body=WHEEL):
        self.statuses, self.body, self.hits = list(statuses), body, 0
        release = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                release.hits += 1
                status = release.statuses.pop(0) if len(release.statuses) > 1 else release.statuses[0]
                self.send_response(status)
                self.end_headers()
                if status == 200:
                    self.wfile.write(release.body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/heinzel_plugin_tools-0.2.1-py3-none-any.whl"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def release(request):
    servers = []

    def start(statuses, body=WHEEL):
        servers.append(FakeRelease(statuses, body))
        return servers[-1]

    yield start
    for server in servers:
        server.close()


def unreachable(_url):
    pytest.fail("the authenticated route was not needed")


def unavailable(_url):
    msg = "no gh, no token"
    raise OSError(msg)


def test_gateway_timeout_then_success_is_retried_with_backoff_and_cached(release, tmp_path):
    server, delays = release([504, 200]), []
    wheel = fetch_verified(server.url, DIGEST, cache=tmp_path, fallback=unreachable, sleep=delays.append)
    assert wheel == tmp_path / f"{DIGEST}.whl"
    assert wheel.read_bytes() == WHEEL
    assert server.hits == 2
    assert len(delays) == 1
    assert 0.5 <= delays[0] <= 1.5, "first backoff is jittered around one second"


def test_persistent_gateway_timeout_uses_the_authenticated_route_after_three_tries(release, tmp_path):
    server, delays, routes = release([504]), [], []
    fallback = lambda url: routes.append(url) or WHEEL  # noqa: E731
    wheel = fetch_verified(server.url, DIGEST, cache=tmp_path, fallback=fallback, sleep=delays.append)
    assert wheel.read_bytes() == WHEEL
    assert server.hits == tooling.ATTEMPTS == 3
    assert routes == [server.url]
    assert len(delays) == 2
    assert delays[1] > delays[0] * 0.66, "backoff grows between attempts"


def test_not_found_is_not_retried(release, tmp_path):
    server = release([404])
    with pytest.raises(ValueError, match="download: HTTP 404; authenticated download: OSError"):
        fetch_verified(server.url, DIGEST, cache=tmp_path, fallback=unavailable, sleep=lambda _: None)
    assert server.hits == 1


def test_hash_mismatch_from_every_route_is_refused_and_never_cached(release, tmp_path):
    server = release([200], body=b"tampered wheel")
    with pytest.raises(ValueError, match="integrity check"):
        fetch_verified(server.url, DIGEST, cache=tmp_path, fallback=lambda _: b"also tampered", sleep=lambda _: None)
    assert list(tmp_path.iterdir()) == []


def test_verified_cache_entry_is_reused_offline(tmp_path):
    (tmp_path / f"{DIGEST}.whl").write_bytes(WHEEL)
    offline = lambda _: pytest.fail("a verified cache entry needs no network")  # noqa: E731
    wheel = fetch_verified("https://example.test/t.whl", DIGEST, cache=tmp_path, get=offline, fallback=offline)
    assert wheel.read_bytes() == WHEEL


def test_corrupt_cache_entry_is_replaced_by_verified_bytes(release, tmp_path):
    (tmp_path / f"{DIGEST}.whl").write_bytes(b"corrupt")
    server = release([200])
    wheel = fetch_verified(server.url, DIGEST, cache=tmp_path, fallback=unreachable, sleep=lambda _: None)
    assert wheel.read_bytes() == WHEEL


def test_unhashed_direct_url_requirement_is_refused(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("toolkit @ https://example.test/toolkit-1.0-py3-none-any.whl\n")
    with pytest.raises(ValueError, match="one --hash=sha256"):
        tooling.local_requirements(requirements, tmp_path, fetch=unreachable)


def test_authenticated_route_refuses_urls_that_are_not_github_release_assets(monkeypatch):
    monkeypatch.setattr(tooling.subprocess, "run", lambda *_, **__: pytest.fail("gh ran for a foreign URL"))
    with pytest.raises(OSError, match="not a GitHub release asset"):
        tooling.authenticated_get("https://example.test/o/r/releases/download/t/x.whl")


def test_authenticated_route_uses_gh_release_download_for_the_pinned_asset(monkeypatch):
    commands = []
    monkeypatch.setattr(tooling.shutil, "which", lambda _: "/fictional/gh")

    def gh(command, **kwargs):
        commands.append(command)
        (Path(command[command.index("-D") + 1]) / "heinzel_plugin_tools-0.2.1-py3-none-any.whl").write_bytes(WHEEL)
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(tooling.subprocess, "run", gh)
    url = tooling.TOOLKIT.split(" @ ")[1].split("#")[0]
    assert tooling.authenticated_get(url) == WHEEL
    assert commands[0][1:8] == [
        "release",
        "download",
        "tools-v0.2.1",
        "-R",
        "helmut-hoffer-von-ankershoffen/heinzel-plugins",
        "-p",
        "heinzel_plugin_tools-0.2.1-py3-none-any.whl",
    ]


def test_api_redirect_to_storage_drops_the_token():
    request = urllib.request.Request("https://api.github.com/repos/o/r/releases/assets/1")
    request.add_header("Authorization", "Bearer fictional-token")
    request.add_header("Accept", "application/octet-stream")
    handler = tooling._NoCredentialRedirect()
    redirected = handler.redirect_request(request, None, 302, "Found", {}, "https://storage.example.test/asset")
    assert redirected is not None
    assert not redirected.has_header("Authorization")
    assert redirected.full_url == "https://storage.example.test/asset"
