# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Install the programs the build needs that are not Python packages, pinned and checksummed, into .tools/bin.

scripts/tools.toml lists them: a version and, per platform, a download and its sha256. Installing one downloads it
(or takes it from the download cache, ~/.cache/heinzel-dev/downloads, keyed by checksum), refuses it unless the
checksum matches, and puts the binary in `.tools/<name>-<version>/`, linked from `.tools/bin/<name>`. make puts
`.tools/bin` first on PATH. Nothing needs sudo or a system package manager; a platform without a pinned file uses the
program on PATH and prints how to install it. Runs on the standard library only, so it works before `uv sync`.

Usage: uv run --script scripts/tools.py COMMAND
    install [GROUP|NAME ...]   install a group's tools or single tools (default: the build group); one line each
    run NAME [ARG ...]         install NAME if needed, then run it with the arguments (git hooks, make targets)
    doctor                     what the build needs, what is missing, and the command that fixes it
    verify                     download every pinned file for every platform and check its checksum and binary
    pin NAME                   download NAME's files for its current version and write their checksums
"""

import argparse
import fnmatch
import hashlib
import http.client
import io
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MANIFEST = HERE / "tools.toml"
TOOLS_DIR = ROOT / ".tools"
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "heinzel-dev" / "downloads"
PLATFORMS = ("darwin-arm64", "darwin-x86_64", "linux-x86_64", "linux-arm64")
GROUPS = {"build": "make install", "demo": "make demo"}  # group: what installs it
TIMEOUT = 120
SHA256 = re.compile(r"[0-9a-f]{64}")
# CA bundles of common Linux distributions, for Pythons whose OpenSSL looks elsewhere.
CA_BUNDLES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt")


class ToolError(Exception):
    """A tool can't be installed; the message says why and what to do."""


@dataclass(frozen=True)
class Download:
    """One pinned file: where it is, its checksum and, for an archive, the binary's path inside it."""

    url: str
    sha256: str
    member: str | None = None


@dataclass(frozen=True)
class Tool:
    """A program from tools.toml."""

    name: str
    version: str
    group: str
    why: str
    home: str
    hints: dict[str, str] = field(default_factory=dict[str, str])
    files: dict[str, Download] = field(default_factory=dict[str, Download])

    def hint(self, platform_: str) -> str:
        """How to install the program yourself on this platform."""
        return self.hints.get(platform_.split("-", maxsplit=1)[0], f"see {self.home}")


@dataclass(frozen=True)
class Result:
    """What installing a tool did: `present`, `installed`, `system` (from PATH) or `missing`."""

    tool: Tool
    status: str
    path: Path | None = None
    note: str = ""

    @property
    def ok(self) -> bool:
        """Whether the tool can run."""
        return self.status != "missing"

    def line(self) -> str:
        """The one line make prints for this tool."""
        label = f"{self.tool.name} {self.tool.version}"
        match self.status:
            case "present":
                return f"✓ {label}"
            case "installed":
                return f"✓ {label} (installed)"
            case "system":
                return f"✓ {self.tool.name} from {self.path} ({self.note})"
            case _:
                return f"✗ {label}: {self.note}"


def current_platform(system: str | None = None, machine: str | None = None) -> str:
    """This machine as a tools.toml platform key, such as `darwin-arm64` or `linux-x86_64`."""
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    arch = {"aarch64": "arm64", "arm64": "arm64", "amd64": "x86_64", "x86_64": "x86_64"}.get(machine, machine)
    return f"{system}-{arch}"


def load(path: Path = MANIFEST, *, checksums: bool = True) -> dict[str, Tool]:
    """The tools of a manifest, validated, with `{version}` and `{file}` filled in.

    `checksums=False` accepts an empty sha256, for `pin` to fill in.

    Raises:
        ToolError: an entry lacks a group, a known platform, a checksum or an https URL.
    """
    tools: dict[str, Tool] = {}
    for name, spec in tomllib.loads(path.read_text(encoding="utf-8")).items():
        version = spec["version"]
        if spec.get("group") not in GROUPS:
            msg = f"{path.name}: {name} needs a group, one of {', '.join(GROUPS)}"
            raise ToolError(msg)
        files: dict[str, Download] = {}
        for key, entry in spec.get("files", {}).items():
            if key not in PLATFORMS:
                msg = f"{path.name}: {name} has unknown platform {key!r}; use one of {', '.join(PLATFORMS)}"
                raise ToolError(msg)
            if not SHA256.fullmatch(entry.get("sha256", "")) and (checksums or entry.get("sha256", "")):
                msg = f"{path.name}: {name} {key} needs a sha256 (64 lowercase hex digits)"
                raise ToolError(msg)
            file = entry.get("file", "").format(version=version)
            url = entry.get("url", spec.get("url", "")).format(version=version, file=file)
            if not url.startswith("https://"):
                msg = f"{path.name}: {name} {key} needs an https url"
                raise ToolError(msg)
            files[key] = Download(url, entry.get("sha256", ""), spec.get("member"))
        tools[name] = Tool(name, version, spec["group"], spec["why"], spec["home"], spec.get("hint", {}), files)
    return tools


def select(tools: dict[str, Tool], names: Iterable[str]) -> list[Tool]:
    """The tools named, or in the groups named, in manifest order.

    Raises:
        ToolError: a name is neither a tool nor a group.
    """
    wanted = set(names) or {"build"}
    unknown = wanted - set(tools) - set(GROUPS)
    if unknown:
        msg = f"no tool or group {', '.join(sorted(unknown))}; tools: {', '.join(tools)}"
        raise ToolError(msg)
    return [tool for tool in tools.values() if tool.name in wanted or tool.group in wanted]


def named(tools: dict[str, Tool], name: str) -> Tool:
    """One tool by name; a group's name is not a tool.

    Raises:
        ToolError: there is no tool of that name.
    """
    if name not in tools:
        msg = f"no tool {name}; tools: {', '.join(tools)}"
        raise ToolError(msg)
    return tools[name]


def ssl_context() -> ssl.SSLContext:
    """The default context, or one with a distribution's CA bundle when this Python's OpenSSL finds none."""
    context = ssl.create_default_context()
    if not os.environ.get("SSL_CERT_FILE") and not context.cert_store_stats()["x509_ca"]:
        bundle = next((path for path in CA_BUNDLES if Path(path).is_file()), None)
        if bundle:
            context.load_verify_locations(bundle)
    return context


def http_get(url: str) -> bytes:
    """The body at a URL, following redirects; proxies come from the usual environment variables."""
    request = urllib.request.Request(url, headers={"User-Agent": "heinzel-dev-tools"})  # noqa: S310 - https only (load)
    with urllib.request.urlopen(request, timeout=TIMEOUT, context=ssl_context()) as response:  # noqa: S310
        return bytes(response.read())


def download_bytes(url: str, get: Callable[[str], bytes] = http_get) -> bytes:
    """The bytes at a URL.

    Raises:
        ToolError: the download failed, including a connection cut short.
    """
    try:
        return get(url)
    except (OSError, http.client.HTTPException) as error:
        msg = f"download failed ({error or type(error).__name__})"
        raise ToolError(msg) from error


def fetch(
    download: Download, cache: Path = CACHE, get: Callable[[str], bytes] = http_get, *, cached_ok: bool = True
) -> bytes:
    """A pinned file's bytes, from the cache or the network, refused unless they match the checksum.

    `cached_ok=False` always downloads (and refreshes the cache), to check the file is still published.

    Raises:
        ToolError: the download failed or doesn't match the checksum.
    """
    cached = cache / download.sha256
    if cached_ok and cached.is_file():
        data = cached.read_bytes()
        if hashlib.sha256(data).hexdigest() == download.sha256:
            return data
    data = download_bytes(download.url, get)
    actual = hashlib.sha256(data).hexdigest()
    if actual != download.sha256:
        msg = f"checksum mismatch for {download.url}: expected {download.sha256}, got {actual}"
        raise ToolError(msg)
    cache.mkdir(parents=True, exist_ok=True)
    _write_atomically(cached, data)
    return data


def extract(data: bytes, download: Download) -> bytes:
    """The binary: the download itself, or the archive member matching `member`.

    Raises:
        ToolError: the archive type is unknown or no member matches.
    """
    if download.member is None:
        return data
    name = download.url.rsplit("/", 1)[-1]
    if name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for info in archive.getmembers():
                if info.isfile() and fnmatch.fnmatch(info.name, download.member):
                    extracted = archive.extractfile(info)
                    if extracted:
                        return extracted.read()
    elif name.endswith((".zip", ".whl")):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.namelist():
                if not member.endswith("/") and fnmatch.fnmatch(member, download.member):
                    return archive.read(member)
    else:
        msg = f"{name}: unknown archive type; use .tar.gz, .zip or .whl, or no member"
        raise ToolError(msg)
    msg = f"{name} has no file matching {download.member!r}"
    raise ToolError(msg)


def _write_atomically(path: Path, data: bytes, mode: int = 0o644) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as temporary:
        temporary.write(data)
    Path(temporary.name).chmod(mode)
    Path(temporary.name).replace(path)


def on_path(name: str, tools_dir: Path = TOOLS_DIR) -> Path | None:
    """The program on PATH outside our own bin directory."""
    own = (tools_dir / "bin").resolve()
    directories = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and Path(d).resolve() != own]
    found = shutil.which(name, path=os.pathsep.join(directories))
    return Path(found) if found else None


def installed(tool: Tool, platform_: str, tools_dir: Path = TOOLS_DIR) -> Path | None:
    """The linked binary when the pinned version for this platform is installed, otherwise None."""
    download = tool.files.get(platform_)
    link = tools_dir / "bin" / tool.name
    stamp = tools_dir / f"{tool.name}-{tool.version}" / ".sha256"
    if download is None or not stamp.is_file() or stamp.read_text(encoding="utf-8").strip() != download.sha256:
        return None
    return link if link.resolve() == (stamp.parent / tool.name).resolve() and link.is_file() else None


def install(
    tool: Tool,
    platform_: str,
    tools_dir: Path = TOOLS_DIR,
    fetch_: Callable[[Download], bytes] = fetch,
) -> Result:
    """Install the pinned binary for this platform unless it is already there; idempotent and safe to interrupt."""
    download = tool.files.get(platform_)
    if download is None:
        found = on_path(tool.name, tools_dir)
        if found:
            return Result(tool, "system", found, f"no pinned build for {platform_}")
        return Result(tool, "missing", note=f"no pinned build for {platform_} and not on PATH; {tool.hint(platform_)}")
    if path := installed(tool, platform_, tools_dir):
        return Result(tool, "present", path)
    try:
        binary = extract(fetch_(download), download)
    except ToolError as error:
        found = on_path(tool.name, tools_dir)
        if found:
            return Result(tool, "system", found, f"the pinned build is unavailable: {error}")
        return Result(tool, "missing", note=f"{error}; or install it yourself: {tool.hint(platform_)}")
    home = tools_dir / f"{tool.name}-{tool.version}"
    home.mkdir(parents=True, exist_ok=True)
    _write_atomically(home / tool.name, binary, 0o755)
    _write_atomically(home / ".sha256", f"{download.sha256}\n".encode())
    bin_dir = tools_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = bin_dir / tool.name
    temporary = bin_dir / f".{tool.name}.{os.getpid()}.link"  # per process: a hook and a target may install at once
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(Path("..") / home.name / tool.name)
    temporary.replace(link)
    for old in tools_dir.glob(f"{tool.name}-*"):
        if old != home and old.is_dir() and (old / tool.name).exists():
            shutil.rmtree(old, ignore_errors=True)
    return Result(tool, "installed", link)


def install_all(tools: Iterable[Tool], platform_: str, tools_dir: Path = TOOLS_DIR) -> int:
    """Install each tool, print one line per tool, and return the exit code."""
    results = [install(tool, platform_, tools_dir) for tool in tools]
    for result in results:
        print(result.line(), file=sys.stdout if result.ok else sys.stderr)
    return 0 if all(result.ok for result in results) else 1


def run(
    tool: Tool,
    platform_: str,
    arguments: list[str],
    tools_dir: Path = TOOLS_DIR,
    fetch_: Callable[[Download], bytes] = fetch,
) -> int:
    """Run a tool, installing it first if needed; only an installation or a failure prints anything (on stderr)."""
    result = install(tool, platform_, tools_dir, fetch_)
    if result.status == "installed" or not result.ok:
        print(result.line(), file=sys.stderr)
    if result.ok and result.path is not None:
        executable = str(result.path)
        os.execv(executable, [executable, *arguments])  # noqa: S606 - a pinned, checksummed binary or the one on PATH
    return 1


def pin(name: str, manifest: Path = MANIFEST, get: Callable[[str], bytes] = http_get) -> list[str]:
    """Download a tool's files for the version in the manifest and write their checksums into it.

    Raises:
        ToolError: the manifest has no such tool, or a download failed.
    """
    files = named(load(manifest, checksums=False), name).files.items()
    checksums = {key: hashlib.sha256(download_bytes(download.url, get)).hexdigest() for key, download in files}
    lines, section = manifest.read_text(encoding="utf-8").splitlines(keepends=True), ""
    changed: list[str] = []
    for index, line in enumerate(lines):
        if header := re.match(r"\[([\w.-]+)\]", line):
            section = header[1]
        key = line.split("=", 1)[0].strip()
        if section == f"{name}.files" and key in checksums:
            lines[index] = re.sub(r'sha256 = "[0-9a-f]*"', f'sha256 = "{checksums[key]}"', line)
            changed.append(f"{key} {checksums[key]}")
    manifest.write_text("".join(lines), encoding="utf-8")
    return changed


def verify(tools: Iterable[Tool]) -> int:
    """Download every pinned file of every platform afresh, check it and find its binary; one line each."""
    failures = 0
    for tool in tools:
        for key, download in tool.files.items():
            try:
                size = len(extract(fetch(download, cached_ok=False), download))
                print(f"✓ {tool.name} {tool.version} {key} ({size // 1024} KiB)")
            except ToolError as error:
                failures += 1
                print(f"✗ {tool.name} {tool.version} {key}: {error}", file=sys.stderr)
    return 1 if failures else 0


# Doctor: what the build needs, and the command that fixes what is missing.

UV_INSTALL = "curl -LsSf https://astral.sh/uv/install.sh | sh"
PREREQUISITES = {  # program: (what for, how to get it on macOS, on Linux)
    "git": ("version control", "xcode-select --install", "install git with your package manager"),
    "make": ("runs every task", "xcode-select --install", "install make with your package manager"),
    "uv": ("Python, the dependencies and every Python tool", UV_INSTALL, UV_INSTALL),
    "bash": ("runs the scripts", "comes with macOS", "install bash with your package manager"),
}


@dataclass(frozen=True)
class Check:
    """One line of `make doctor`: passed (True), missing (False) or optional and absent (None)."""

    section: str
    state: bool | None
    text: str
    fix: str = ""

    def line(self) -> str:
        """The check as printed, with its fix when it didn't pass."""
        mark = {True: "✓", False: "✗", None: "-"}[self.state]
        return f"  {mark} {self.text}" + (f"  →  {self.fix}" if self.fix and self.state is not True else "")


def _output(*command: str) -> tuple[int, str]:
    """The exit code and first line of output of a command, 127 when it can't start."""
    try:
        done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=60)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, str(error)
    lines = (done.stdout or done.stderr).strip().splitlines()
    return done.returncode, lines[0] if lines else ""


def _version(text: str) -> tuple[int, ...]:
    found = re.search(r"\d+(?:\.\d+)+", text)
    return tuple(int(part) for part in found.group().split(".")) if found else ()


def tool_check(tool: Tool, platform_: str, tools_dir: Path = TOOLS_DIR) -> Check:
    """Whether a tool is installed; only a missing `build` tool fails."""
    installer, required = GROUPS[tool.group], tool.group == "build"
    section = f"Tools: {tool.group}" + ("" if required else f" (optional; {installer})")
    if installed(tool, platform_, tools_dir):
        return Check(section, state=True, text=f"{tool.name} {tool.version}")
    system = on_path(tool.name, tools_dir)
    if tool.files.get(platform_) is None:
        text = f"{tool.name}: no pinned build for {platform_}, " + (f"using {system}" if system else "not on PATH")
        return Check(section, True if system else (False if required else None), text, tool.hint(platform_))
    if system:  # what install falls back to when the download fails
        return Check(section, None, f"{tool.name} {tool.version}: not installed, {system} stands in", installer)
    return Check(section, False if required else None, f"{tool.name} {tool.version}: not installed", installer)


def checks(
    platform_: str,
    tools: dict[str, Tool],
    tools_dir: Path = TOOLS_DIR,
    which: Callable[[str], str | None] = shutil.which,
    output: Callable[..., tuple[int, str]] = _output,
) -> list[Check]:
    """Everything `make doctor` reports, in order."""
    darwin = platform_.startswith("darwin")
    found: list[Check] = []
    section = f"Prerequisites ({platform_})"
    for program, (why, on_darwin, on_linux) in PREREQUISITES.items():
        version = output(program, "--version")[1] if which(program) else ""
        found.append(
            Check(
                section,
                bool(version),
                f"{program} ({why}): {version or 'not found'}",
                on_darwin if darwin else on_linux,
            )
        )
    pyproject = ROOT / "pyproject.toml"
    project = tomllib.loads(pyproject.read_text(encoding="utf-8")) if pyproject.is_file() else {}
    required = project.get("tool", {}).get("uv", {}).get("required-version", "")
    if re.fullmatch(r">=\s*[\d.]+", required) and which("uv"):  # other specifiers: `uv sync --check` below reports
        have = _version(output("uv", "--version")[1])
        found.append(Check(section, have >= _version(required), f"uv is {required} (pyproject.toml)", "uv self update"))

    section = "Project"
    if project and which("uv"):
        synced = output("uv", "sync", "--all-groups", "--check", "--quiet")[0] == 0
        found.append(Check(section, synced, ".venv matches uv.lock, with every dependency group", "make install"))
    code, hooks = output("git", "rev-parse", "--git-path", "hooks")
    pre_commit = ROOT / hooks / "pre-commit"
    hooked = code == 0 and pre_commit.is_file() and "prek" in pre_commit.read_text(encoding="utf-8", errors="replace")
    found.append(Check(section, hooked, "git hooks: pre-commit, commit-msg, pre-push (prek)", "make setup"))

    makefile = ROOT / "Makefile"
    targets = re.findall(r"(?m)^([\w-]+):", makefile.read_text(encoding="utf-8")) if makefile.is_file() else []
    for group, installer in GROUPS.items():
        if group == "build" or installer.removeprefix("make ") in targets:  # a plugin has no `make demo`
            found += [tool_check(tool, platform_, tools_dir) for tool in tools.values() if tool.group == group]

    section = "Accounts"
    gh = (installed(tools["gh"], platform_, tools_dir) if "gh" in tools else None) or on_path("gh", tools_dir)
    if gh:
        logged_in = output(str(gh), "auth", "status", "--hostname", "github.com")[0] == 0
        text = "gh logged in to github.com (for make pr, make ci-logs)"
        found.append(Check(section, logged_in or None, text, f"{gh} auth login"))
    return found


def doctor(platform_: str, tools: dict[str, Tool]) -> int:
    """Print what the build needs and the command that fixes what is missing; 1 when something required is."""
    section = ""
    results = checks(platform_, tools)
    for check in results:
        if check.section != section:
            section = check.section
            print(section)
        print(check.line())
    missing = sum(check.state is False for check in results)
    print(
        f"{missing} missing: run the commands after the arrows." if missing else "Everything the build needs is here."
    )
    return 1 if missing else 0


def main(argv: list[str] | None = None) -> int:
    """Run a command; the exit code is 1 when something is missing or broken."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install").add_argument("names", nargs="*")
    runner = commands.add_parser("run")
    runner.add_argument("name")
    runner.add_argument("arguments", nargs=argparse.REMAINDER)
    commands.add_parser("doctor")
    commands.add_parser("verify")
    commands.add_parser("pin").add_argument("name")
    arguments = parser.parse_args(argv)
    platform_ = current_platform()
    try:
        tools = load(checksums=arguments.command != "pin")
        match arguments.command:
            case "install":
                return install_all(select(tools, arguments.names), platform_)
            case "run":
                return run(named(tools, arguments.name), platform_, arguments.arguments)
            case "doctor":
                return doctor(platform_, tools)
            case "verify":
                return verify(tools.values())
            case _:
                print(*pin(arguments.name), sep="\n")
                return 0
    except ToolError as error:
        print(f"✗ {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
