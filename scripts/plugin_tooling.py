# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Run the released plugin toolkit from a disposable, hash-locked wheel environment, without installing core."""

import argparse
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import IO
from urllib.parse import quote, urlsplit, urlunsplit

TOOLKIT = (
    "heinzel-plugin-tools @ https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/"
    "tools-v0.2.1/heinzel_plugin_tools-0.2.1-py3-none-any.whl"
    "#sha256=eebaf555c0c3b39cb13d28ae5ea146ec459e06d1b96cf8ba4499ffea4247aab7"
)

REQUIREMENTS = Path(__file__).with_name("plugin-tooling-requirements.txt")
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "hz" / "plugin-tooling"
"""Content-addressed: `<sha256>.whl`, written only after the digest matched, so a verified wheel works offline."""
ATTEMPTS = 3
LIMIT = 128 * 1024 * 1024
TRANSIENT = frozenset({429, 500, 502, 503, 504})
RELEASE_ASSET = re.compile(
    r"/(?P<repo>[\w.-]+/[\w.-]+)/releases/download/(?P<tag>[^/-][^/]*)/(?P<name>[^/-][^/]*\.whl)"
)

# Split callable argument lists to avoid the shared template's variable delimiter.
HOSTED_PAUSE = 20
"""Seconds (jittered) before the second direct round when a hosted step has no token for the authenticated route."""


class NoCredentialsError(OSError):
    """The authenticated route cannot work here: a hosted CI step without a token for gh or the API."""


def hosted_without_token() -> bool:
    """A GitHub Actions step that passes no GH_TOKEN or GITHUB_TOKEN, where gh cannot authenticate."""
    return os.environ.get("GITHUB_ACTIONS") == "true" and not (
        os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    )


type Fetch = Callable[
    [str],
    bytes,
]
type Sleep = Callable[
    [float],
    None,
]
type Source = Callable[
    [],
    bytes,
]
type FetchWheel = Callable[
    [str, str],
    Path,
]


def anonymous_get(url: str) -> bytes:
    """One HTTPS GET of a public release asset, bounded in size."""
    request = urllib.request.Request(url, headers={"User-Agent": "heinzel-plugin-tooling"})  # noqa: S310 - https only
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - https only; digest checked by caller
        return bytes(response.read(LIMIT + 1))


def retried(get: Fetch, url: str, sleep: Sleep = time.sleep) -> bytes:
    """The body after at most three attempts, with jittered exponential backoff for gateway and transport errors.

    Raises:
        urllib.error.HTTPError: the last attempt's status, or a status that is not transient (a 404).
        OSError: the last attempt's connection failed, or TLS verification failed.
        http.client.HTTPException: the last response was cut short.
    """
    attempt = 1
    while True:
        try:
            return get(url)
        except urllib.error.HTTPError as error:
            error.close()
            if error.code not in TRANSIENT or attempt == ATTEMPTS:
                raise
        except (OSError, http.client.HTTPException) as error:
            if attempt == ATTEMPTS or isinstance(error, ssl.SSLCertVerificationError):
                raise
        sleep(2 ** (attempt - 1) * (0.5 + secrets.randbelow(1001) / 1000))  # jitter spreads the retries
        attempt += 1


class _NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    """Follow the API's redirect to the storage host without forwarding the token there."""

    def redirect_request(  # noqa: PLR0913, PLR0917 - urllib's signature
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def _token() -> str | None:
    """A GitHub token for this process only: the environment's, else `gh auth token`; never printed or logged."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    gh = shutil.which("gh")
    if not token and gh:
        try:
            # Resolved gh, fixed arguments; the token stays in this process
            result = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, check=False, timeout=30)  # noqa: S603
        except (OSError, subprocess.TimeoutExpired):
            return None
        token = result.stdout.strip() if result.returncode == 0 else None
    return token or None


def authenticated_get(url: str) -> bytes:
    """The same release asset through GitHub's authenticated API route, which survives anonymous-edge 504s.

    `gh release download` when gh is installed, else the REST asset endpoint with a token from the environment or gh.

    Raises:
        NoCredentialsError: a hosted CI step passes no token, so no authenticated route can work.
        OSError: no route works (not a GitHub release asset, no gh and no token, or the API failed).
    """
    match = RELEASE_ASSET.fullmatch(urlsplit(url).path) if urlsplit(url).hostname == "github.com" else None
    if match is None:
        msg = "not a GitHub release asset; no authenticated route"
        raise OSError(msg)
    if hosted_without_token():
        msg = "authenticated fallback skipped: this hosted CI step has no GH_TOKEN"
        raise NoCredentialsError(msg)
    repo, tag, name = match["repo"], match["tag"], match["name"]
    gh = shutil.which("gh")
    if gh:
        with tempfile.TemporaryDirectory(prefix="hz-plugin-toolkit-gh-") as directory:
            try:
                # Resolved gh, arguments parsed from the pinned URL (no capture starts with "-")
                result = subprocess.run(  # noqa: S603
                    [gh, "release", "download", tag, "-R", repo, "-p", name, "-D", directory],
                    capture_output=True,
                    check=False,
                    timeout=120,
                )
            except (OSError, subprocess.TimeoutExpired):
                result = None  # an unexecutable or hanging gh leaves the token route
            wheel = Path(directory) / name
            if result and result.returncode == 0 and wheel.is_file() and wheel.stat().st_size <= LIMIT:
                return wheel.read_bytes()
    token = _token()
    if token is None:
        msg = "authenticated fallback needs gh or GH_TOKEN"
        raise OSError(msg)
    opener = urllib.request.build_opener(_NoCredentialRedirect)
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "heinzel-plugin-tooling"}
    api = f"https://api.github.com/repos/{repo}/releases/tags/{quote(tag)}"
    request = urllib.request.Request(api, headers={**headers, "Accept": "application/vnd.github+json"})
    with opener.open(request, timeout=60) as response:
        assets = json.loads(response.read())["assets"]
    asset = next((item for item in assets if item.get("name") == name), None)
    if asset is None:
        msg = f"release {tag} has no asset {name}"
        raise OSError(msg)
    request = urllib.request.Request(asset["url"], headers={**headers, "Accept": "application/octet-stream"})  # noqa: S310
    if not request.full_url.startswith("https://api.github.com/"):
        msg = "unexpected asset endpoint"
        raise OSError(msg)
    with opener.open(request, timeout=60) as response:
        return bytes(response.read(LIMIT + 1))


def fetch_verified(  # noqa: PLR0913 - every source injectable for tests
    url: str,
    digest: str,
    *,
    cache: Path | None = None,
    get: Fetch = anonymous_get,
    fallback: Fetch = authenticated_get,
    sleep: Sleep = time.sleep,
) -> Path:
    """A wheel file whose SHA-256 equals `digest`: from the cache, the URL (retried) or the authenticated route.

    Nothing unverified is cached or returned.

    Raises:
        ValueError: the bytes from every route failed the digest, or no route delivered them.
    """
    cache = cache or CACHE
    cached = cache / f"{digest}.whl"
    if cached.is_file() and hashlib.sha256(cached.read_bytes()).hexdigest() == digest:
        return cached
    failures: list[str] = []

    def authenticated() -> bytes:
        try:
            return fallback(url)
        except NoCredentialsError:
            # No token on a hosted step: one more direct round after a longer pause instead
            failures.append("authenticated download: skipped, this hosted CI step has no GH_TOKEN")
            sleep(HOSTED_PAUSE * (0.5 + secrets.randbelow(1001) / 1000))
            return retried(get, url, sleep)

    routes: tuple[tuple[str, Source], ...] = (
        ("download", lambda: retried(get, url, sleep)),
        ("authenticated download", authenticated),
    )
    for route, source in routes:
        try:
            content = source()
        except (OSError, http.client.HTTPException, ValueError, KeyError) as error:
            # Status code or exception type only: error text may echo request details
            reason = f"HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else type(error).__name__
            failures.append(f"{route}: {reason}")
            continue
        if len(content) <= LIMIT and hashlib.sha256(content).hexdigest() == digest:
            cache.mkdir(mode=0o700, parents=True, exist_ok=True)
            # O_EXCL and 0600 until complete: never follows a planted symlink or exposes a partial file
            with tempfile.NamedTemporaryFile(dir=cache, prefix=f".{digest}.", delete=False) as partial:
                partial.write(content)
            Path(partial.name).chmod(0o644)
            Path(partial.name).replace(cached)
            return cached
        failures.append(f"{route}: integrity check failed")
    msg = f"Candidate toolkit wheel failed its integrity check or download ({'; '.join(failures)})"
    raise ValueError(msg)


def local_requirements(requirements: Path, directory: Path, fetch: FetchWheel | None = None) -> Path:
    """The requirements with each hashed `name @ https://...whl` line pointing at its verified local copy.

    Each rewritten line keeps its `--hash`, so `uv pip install --require-hashes` checks the wheel again; every other
    line is copied unchanged.

    Raises:
        ValueError: a direct URL line has no single SHA-256 hash.
    """
    fetch = fetch or fetch_verified
    lines = []
    for entry in requirements.read_text(encoding="utf-8").splitlines():
        line = entry
        match = re.fullmatch(
            r"(?P<name>[\w.-]+) @ (?P<url>https://\S+\.whl) --hash=sha256:(?P<digest>[a-f0-9]{64})", line
        )
        if " @ " in line and not line.startswith("#") and match is None:
            msg = f"Pin {line.split(' @ ', 1)[0]} to one wheel URL with one --hash=sha256"
            raise ValueError(msg)
        if match:
            # uv reads the version from the file name, so the verified copy keeps the release's wheel name
            wheel = directory / Path(urlsplit(match["url"]).path).name
            shutil.copyfile(fetch(match["url"], match["digest"]), wheel)
            line = f"{match['name']} @ {wheel.resolve().as_uri()} --hash=sha256:{match['digest']}"
        lines.append(line)
    rewritten = directory / requirements.name
    rewritten.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rewritten


def candidate_wheel(spec: str, directory: Path) -> Path:
    """Resolve an explicit developer wheel and enforce a remote URL's declared digest.

    Raises:
        ValueError: If the candidate is not a local wheel or an HTTPS wheel with a SHA-256 digest.
    """
    value = spec.rsplit(" @ ", 1)[-1]
    url = urlsplit(value)
    if url.scheme:
        expected = url.fragment.removeprefix("sha256=")
        if (
            url.scheme != "https"
            or not url.fragment.startswith("sha256=")
            or not re.fullmatch(r"[a-f0-9]{64}", expected)
            or not url.path.endswith(".whl")
        ):
            msg = "Use a local candidate wheel or an HTTPS wheel URL with #sha256=<digest>"
            raise ValueError(msg)
        # Explicit developer HTTPS candidate, bytes verified before installation
        verified = fetch_verified(urlunsplit(url._replace(fragment="")), expected)
        wheel = directory / Path(url.path).name
        shutil.copyfile(verified, wheel)
        return wheel
    wheel = Path(value).resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        msg = "Select an existing local toolkit wheel"
        raise ValueError(msg)
    return wheel


def within_interface(program: str, arguments: list[str]) -> bool:
    """Whether the arguments start a toolkit subcommand or a toolkit module, never an interpreter or tool option."""
    match program, arguments:
        case "hz-plugin", [command, *_]:
            return re.fullmatch(r"[a-z][a-z0-9-]*", command) is not None
        case "python", ["-m", module, *_]:
            return re.fullmatch(r"heinzel_plugin_tools(?:\.[a-z_]+)+", module) is not None
        case _:
            return False


def run(program: str, arguments: list[str], requirements: Path = REQUIREMENTS, toolkit: str = TOOLKIT) -> int:
    """Install verified wheels before running the selected local developer command.

    Raises:
        ValueError: If the executable or its first argument is outside the toolkit interface.
        RuntimeError: If uv is unavailable.
    """
    if program not in {"hz-plugin", "python"}:
        msg = "Choose hz-plugin or python from the verified toolkit environment"
        raise ValueError(msg)
    if not within_interface(program, arguments):
        msg = "Run a toolkit subcommand (hz-plugin <command>) or module (python -m heinzel_plugin_tools.<module>)"
        raise ValueError(msg)
    uv = shutil.which("uv")
    if uv is None:
        msg = "Install uv before running the plugin toolkit"
        raise RuntimeError(msg)
    with tempfile.TemporaryDirectory(prefix="hz-plugin-toolkit-") as temporary:
        environment = Path(temporary)
        interpreter = environment / "bin/python"
        # Resolved uv, disposable environment
        subprocess.run([uv, "venv", "--python", "3.14", str(environment)], check=True, timeout=120)  # noqa: S603
        if toolkit != TOOLKIT:
            selection = [str(candidate_wheel(toolkit, environment))]
        else:
            selection = ["--require-hashes", "--requirements", str(local_requirements(requirements, environment))]
        # Verified default closure or an explicitly selected developer wheel
        subprocess.run(  # noqa: S603
            [uv, "pip", "install", "--no-build", "--python", str(interpreter), *selection],
            check=True,
            timeout=300,
        )
        # Like `uv run`: the environment's programs (typos, the interpreter) come first on PATH for the toolkit.
        env = {
            **os.environ,
            "PATH": f"{environment / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
            "VIRTUAL_ENV": str(environment),
        }
        # Allowlisted executable in the verified environment, intentional local argv
        return subprocess.run(  # noqa: S603
            [str(environment / "bin" / program), *arguments],
            check=False,
            env=env,
            timeout=1800,
        ).returncode


def main() -> int:
    """Keep the executable selection within the installed toolkit environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolkit", default=TOOLKIT)
    parser.add_argument("program", choices=["hz-plugin", "python"])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    return run(args.program, args.arguments, toolkit=args.toolkit)


if __name__ == "__main__":
    raise SystemExit(main())
