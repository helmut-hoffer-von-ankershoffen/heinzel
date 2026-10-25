"""scripts/tools.py: the pinned, checksummed programs `make install` puts in .tools/bin (ADR 0036)."""

import hashlib
import http.client
import importlib.util
import io
import re
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "tools.py"
spec = importlib.util.spec_from_file_location("tools", SCRIPT)
assert spec
assert spec.loader
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)

BINARY = b"#!/bin/sh\necho fake\n"
HEAD = '[x]\nversion = "1"\ngroup = "build"\nwhy = "w"\nhome = "h"\n'  # a manifest entry, up to its files
ZERO = "0" * 64


def tar_gz(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def zipped(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tool(data: bytes, file: str = "fake.tar.gz", member: str | None = "fake", version: str = "1.0.0", **extra):
    download = tools.Download(f"https://example.com/{file}", sha(data), member)
    defaults = {
        "group": "build",
        "why": "tests",
        "home": "https://example.com",
        "hints": {"darwin": "brew install fake"},
    }
    return tools.Tool("fake", version, **(defaults | extra), files={"linux-x86_64": download})


class Network:
    """A fake network: serves bytes by URL and counts the requests."""

    def __init__(self, files: dict[str, bytes]):
        self.files, self.requests = files, []

    def get(self, url: str) -> bytes:
        self.requests.append(url)
        if url not in self.files:
            msg = f"no route to {url}"
            raise OSError(msg)
        return self.files[url]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Downloads and the cache in tmp_path, nothing on PATH."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    return tmp_path


def installer(network: Network, cache: Path):
    return lambda download: tools.fetch(download, cache, network.get)


# The manifest


def test_the_manifest_loads_and_pins_every_build_tool_on_every_platform():
    manifest = tools.load()
    assert {"gitleaks", "gh"} <= {name for name, tool in manifest.items() if tool.group == "build"}
    for tool_ in manifest.values():
        if tool_.group in tools.REQUIRED:
            assert set(tool_.files) == set(tools.PLATFORMS), f"{tool_.name} must be pinned for every platform"
        assert tool_.version
        assert tool_.why
        assert all(download.url.startswith("https://") for download in tool_.files.values())


@pytest.mark.parametrize(
    ("spec", "problem"),
    [
        ('[x]\nversion = "1"\nwhy = "w"\nhome = "h"\n', "needs a group"),
        (HEAD + "[x.files]\nwindows-x86 = {}\n", "unknown platform"),
        (
            HEAD + 'url = "https://e/{file}"\n[x.files]\nlinux-arm64 = { file = "x", sha256 = "abc" }\n',
            "needs a sha256",
        ),
        (HEAD + 'url = "http://e/{file}"\n[x.files]\nlinux-arm64 = { file = "x", sha256 = "' + ZERO + '" }\n', "https"),
    ],
)
def test_a_broken_manifest_entry_is_refused(tmp_path, spec, problem):
    manifest = tmp_path / "tools.toml"
    manifest.write_text(spec, encoding="utf-8")
    with pytest.raises(tools.ToolError, match=problem):
        tools.load(manifest)


def test_urls_are_filled_in_with_version_and_file(tmp_path):
    manifest = tmp_path / "tools.toml"
    manifest.write_text(
        '[x]\nversion = "2.1"\ngroup = "demo"\nwhy = "w"\nhome = "h"\nurl = "https://e/v{version}/{file}"\n'
        f'[x.files]\nlinux-arm64 = {{ file = "x_{{version}}.tar.gz", sha256 = "{"0" * 64}" }}\n'
        f'darwin-arm64 = {{ url = "https://other/x", sha256 = "{"1" * 64}" }}\n',
        encoding="utf-8",
    )
    files = tools.load(manifest)["x"].files
    assert files["linux-arm64"].url == "https://e/v2.1/x_2.1.tar.gz"
    assert files["darwin-arm64"].url == "https://other/x"


def test_select_picks_groups_and_names_and_defaults_to_build():
    manifest = tools.load()
    assert [t.name for t in tools.select(manifest, [])] == [t.name for t in manifest.values() if t.group == "build"]
    assert [t.name for t in tools.select(manifest, ["jq"])] == ["jq"]
    assert {t.group for t in tools.select(manifest, ["demo"])} == {"demo"}
    with pytest.raises(tools.ToolError, match="no tool or group"):
        tools.select(manifest, ["nope"])


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Darwin", "arm64", "darwin-arm64"),
        ("Darwin", "x86_64", "darwin-x86_64"),
        ("Linux", "x86_64", "linux-x86_64"),
        ("Linux", "aarch64", "linux-arm64"),
        ("Linux", "amd64", "linux-x86_64"),
    ],
)
def test_platforms_are_named_as_in_the_manifest(system, machine, expected):
    assert tools.current_platform(system, machine) == expected


# Downloads and archives


def test_fetch_refuses_a_download_whose_checksum_differs_and_caches_nothing(tmp_path):
    download = tools.Download("https://example.com/x", "0" * 64)
    with pytest.raises(tools.ToolError, match="checksum mismatch"):
        tools.fetch(download, tmp_path / "cache", Network({"https://example.com/x": b"tampered"}).get)
    assert not (tmp_path / "cache" / ("0" * 64)).exists()


def test_fetch_uses_the_cache_by_checksum_and_refetches_a_corrupt_entry(tmp_path):
    data = b"payload"
    download = tools.Download("https://example.com/x", sha(data))
    network = Network({download.url: data})
    assert tools.fetch(download, tmp_path, network.get) == data
    assert tools.fetch(download, tmp_path, network.get) == data
    assert len(network.requests) == 1
    (tmp_path / download.sha256).write_bytes(b"corrupt")
    assert tools.fetch(download, tmp_path, network.get) == data
    assert len(network.requests) == 2


def test_fetch_reports_a_failed_download(tmp_path):
    with pytest.raises(tools.ToolError, match="download failed"):
        tools.fetch(tools.Download("https://example.com/gone", "0" * 64), tmp_path, Network({}).get)


def test_fetch_reports_a_download_cut_short(tmp_path):
    def cut_short(url: str) -> bytes:
        partial = b"partial"
        raise http.client.IncompleteRead(partial, 100)

    with pytest.raises(tools.ToolError, match="download failed"):
        tools.fetch(tools.Download("https://example.com/big", ZERO), tmp_path, cut_short)


def test_fetch_can_ignore_the_cache_to_check_the_file_is_still_published(tmp_path):
    data = b"payload"
    download = tools.Download("https://example.com/x", sha(data))
    tools.fetch(download, tmp_path, Network({download.url: data}).get)
    with pytest.raises(tools.ToolError, match="download failed"):
        tools.fetch(download, tmp_path, Network({}).get, cached_ok=False)


@pytest.mark.parametrize(
    ("file", "member", "archive"),
    [
        ("x.tar.gz", "x", tar_gz({"x": BINARY, "README.md": b"no"})),
        ("x.zip", "*/bin/x", zipped({"x_1/LICENSE": b"no", "x_1/bin/x": BINARY})),
        ("x.whl", "pkg/binaries/x-*", zipped({"pkg/__init__.py": b"", "pkg/binaries/x-linux-v7": BINARY})),
        ("x-linux-amd64", None, BINARY),
    ],
    ids=["tar.gz", "zip", "wheel", "binary"],  # the archives' bytes hold timestamps; test ids must not
)
def test_extract_finds_the_binary_in_each_kind_of_download(file, member, archive):
    assert tools.extract(archive, tools.Download(f"https://e/{file}", sha(archive), member)) == BINARY


def test_extract_says_when_the_archive_lacks_the_binary():
    archive = tar_gz({"other": b"x"})
    with pytest.raises(tools.ToolError, match="no file matching"):
        tools.extract(archive, tools.Download("https://e/x.tar.gz", sha(archive), "x"))


# Installing


def test_install_links_the_binary_and_is_idempotent(isolated):
    archive = tar_gz({"fake": BINARY})
    fake = tool(archive)
    network = Network({fake.files["linux-x86_64"].url: archive})
    tools_dir = isolated / ".tools"
    first = tools.install(fake, "linux-x86_64", tools_dir, installer(network, isolated / "cache"))
    assert first.status == "installed"
    link = tools_dir / "bin" / "fake"
    assert link.is_symlink()
    assert link.read_bytes() == BINARY
    assert link.stat().st_mode & 0o111
    second = tools.install(fake, "linux-x86_64", tools_dir, installer(network, isolated / "cache"))
    assert second.status == "present"
    assert second.line() == "✓ fake 1.0.0"
    assert len(network.requests) == 1


def test_a_new_version_replaces_the_old_one(isolated):
    old, new = tar_gz({"fake": b"old"}), tar_gz({"fake": b"new"})
    network = Network({"https://example.com/old.tar.gz": old, "https://example.com/new.tar.gz": new})
    tools_dir = isolated / ".tools"
    tools.install(tool(old, "old.tar.gz"), "linux-x86_64", tools_dir, installer(network, isolated / "cache"))
    result = tools.install(
        tool(new, "new.tar.gz", version="2.0.0"), "linux-x86_64", tools_dir, installer(network, isolated / "cache")
    )
    assert result.status == "installed"
    assert (tools_dir / "bin" / "fake").read_bytes() == b"new"
    assert sorted(path.name for path in tools_dir.iterdir()) == ["bin", "fake-2.0.0"]


def test_a_tampered_download_installs_nothing_and_says_how_to_install_it_yourself(isolated):
    fake = tool(b"the real one", "fake", member=None)
    network = Network({fake.files["linux-x86_64"].url: b"something else"})
    result = tools.install(fake, "linux-x86_64", isolated / ".tools", installer(network, isolated / "cache"))
    assert result.status == "missing"
    assert "checksum mismatch" in result.note
    assert "see https://example.com" in result.note
    assert not (isolated / ".tools" / "bin" / "fake").exists()


def test_a_platform_without_a_pinned_build_uses_the_program_on_path(isolated, monkeypatch):
    fake = tool(BINARY)
    assert tools.install(fake, "darwin-arm64", isolated / ".tools").line() == (
        "✗ fake 1.0.0: no pinned build for darwin-arm64 and not on PATH; brew install fake"
    )
    system = isolated / "system"
    system.mkdir()
    (system / "fake").write_bytes(BINARY)
    (system / "fake").chmod(0o755)
    monkeypatch.setenv("PATH", str(system))
    result = tools.install(fake, "darwin-arm64", isolated / ".tools")
    assert result.status == "system"
    assert result.path == system / "fake"


def test_the_program_on_path_does_not_count_when_it_is_our_own_link(isolated, monkeypatch):
    own = isolated / ".tools" / "bin"
    own.mkdir(parents=True)
    (own / "fake").write_bytes(BINARY)
    (own / "fake").chmod(0o755)
    monkeypatch.setenv("PATH", str(own))
    assert tools.on_path("fake", isolated / ".tools") is None


def test_pin_writes_the_checksums_of_the_current_version(tmp_path):
    manifest = tmp_path / "tools.toml"
    manifest.write_text(
        '[x]\nversion = "2"\ngroup = "build"\nwhy = "w"\nhome = "h"\nurl = "https://e/{file}"\n'
        f'[x.files]\nlinux-arm64 = {{ file = "x-{{version}}-arm", sha256 = "{"0" * 64}" }}\n'
        f'[y]\nversion = "1"\ngroup = "build"\nwhy = "w"\nhome = "h"\nurl = "https://e/{{file}}"\n'
        f'[y.files]\nlinux-arm64 = {{ file = "y", sha256 = "{"0" * 64}" }}\n',
        encoding="utf-8",
    )
    changed = tools.pin("x", manifest, Network({"https://e/x-2-arm": b"new"}).get)
    assert changed == [f"linux-arm64 {sha(b'new')}"]
    loaded = tools.load(manifest)
    assert loaded["x"].files["linux-arm64"].sha256 == sha(b"new")
    assert loaded["y"].files["linux-arm64"].sha256 == "0" * 64


def test_pin_fills_in_a_new_entry_and_refuses_an_unknown_tool(tmp_path):
    manifest = tmp_path / "tools.toml"
    manifest.write_text(HEAD + 'url = "https://e/{file}"\n[x.files]\nlinux-arm64 = { file = "x", sha256 = "" }\n')
    with pytest.raises(tools.ToolError, match="needs a sha256"):
        tools.load(manifest)
    assert tools.pin("x", manifest, Network({"https://e/x": b"x"}).get) == [f"linux-arm64 {sha(b'x')}"]
    with pytest.raises(tools.ToolError, match="no tool y"):
        tools.pin("y", manifest, Network({}).get)
    with pytest.raises(tools.ToolError, match="download failed"):
        tools.pin("x", manifest, Network({}).get)


# Doctor


def test_doctor_names_the_command_that_fixes_each_missing_piece(isolated):
    found = tools.checks(
        "linux-x86_64",
        {"fake": tool(BINARY)},
        isolated / ".tools",
        which=lambda _: None,
        output=lambda *_: (1, ""),
        web_app=isolated / "web" / "package.json",
    )
    missing = {check.text.split(":")[0]: check.fix for check in found if check.state is False}
    assert missing["git (version control)"] == "install git with your package manager"
    assert missing["uv (Python, the dependencies and every Python tool)"].startswith("curl -LsSf https://astral.sh/")
    assert missing["fake 1.0.0"] == "make install"
    assert missing["git hooks"] == "make setup"


def test_doctor_passes_when_everything_is_there(isolated):
    archive = tar_gz({"fake": BINARY})
    fake = tool(archive)
    network = Network({fake.files["linux-x86_64"].url: archive})
    tools.install(fake, "linux-x86_64", isolated / ".tools", installer(network, isolated / "cache"))
    found = tools.checks(
        "linux-x86_64",
        {"fake": fake},
        isolated / ".tools",
        which=lambda name: f"/usr/bin/{name}",
        output=lambda *command: (0, "uv 99.0.0") if command[0] == "uv" else (0, f"{command[0]} 1.0"),
        web_app=isolated / "web" / "package.json",
    )
    # The hooks of this test run's checkout are whatever they are; everything else passes.
    failed = [check.text for check in found if check.state is False and not check.text.startswith("git hooks")]
    assert failed == []
    assert any(check.text == "fake 1.0.0" and check.state for check in found)


def test_optional_tools_never_fail_the_doctor(isolated):
    found = tools.checks(
        "linux-x86_64",
        {"fake": tool(BINARY, group="demo")},
        isolated / ".tools",
        which=lambda name: f"/usr/bin/{name}",
        output=lambda *_: (0, "1.0"),
        web_app=isolated / "web" / "package.json",
    )
    (demo,) = [check for check in found if check.text.startswith("fake")]
    assert demo.state is None
    assert demo.line().startswith("  - fake 1.0.0: not installed  →  make demo")


# Where the tools are used


def test_make_install_installs_the_build_tools_and_puts_them_first_on_path():
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "export PATH := $(CURDIR)/.tools/bin:$(PATH)" in makefile
    install = re.search(r"^install:.*\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    assert install
    assert "uv sync --all-groups" in install[1]
    assert "$(TOOLS) install" in install[1]
    assert re.search(r"^setup: install\b", makefile, re.MULTILINE)


def test_the_pinned_tools_are_run_through_tools_py_not_downloaded_on_their_own():
    """One pin per tool: hooks, make and CI take gitleaks from scripts/tools.toml, never from a second source."""
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    assert all("gitleaks" not in repo["repo"] for repo in config["repos"])
    (hook,) = [hook for repo in config["repos"] for hook in repo.get("hooks", []) if hook["id"] == "gitleaks"]
    assert hook["entry"].startswith("uv run --quiet --script scripts/tools.py run gitleaks ")
    workflows = "".join(path.read_text(encoding="utf-8") for path in (REPO / ".github" / "workflows").glob("*.yml"))
    assert "releases/download" not in workflows, "install pinned binaries through scripts/tools.toml"


def test_tools_are_gitignored():
    assert ".tools/" in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_run_installs_on_first_use_then_hands_over_to_the_binary(isolated, monkeypatch, capsys):
    archive = tar_gz({"fake": BINARY})
    fake = tool(archive)
    fetch = installer(Network({fake.files["linux-x86_64"].url: archive}), isolated / "cache")
    executed = []
    monkeypatch.setattr(tools.os, "execv", lambda path, argv: executed.append((path, argv)))
    tools.run(fake, "linux-x86_64", ["git", "--staged"], isolated / ".tools", fetch)
    tools.run(fake, "linux-x86_64", ["git"], isolated / ".tools", fetch)
    link = str(isolated / ".tools" / "bin" / "fake")
    assert executed == [(link, [link, "git", "--staged"]), (link, [link, "git"])]
    assert capsys.readouterr().err == "✓ fake 1.0.0 (installed)\n"  # once, on stderr: stdout is the tool's


def test_a_group_name_is_not_a_tool_to_run():
    with pytest.raises(tools.ToolError, match="no tool build"):
        tools.named(tools.load(), "build")


def test_doctor_counts_a_program_on_path_as_a_stand_in_not_a_failure(isolated, monkeypatch):
    system = isolated / "system"
    system.mkdir()
    (system / "fake").write_bytes(BINARY)
    (system / "fake").chmod(0o755)
    monkeypatch.setenv("PATH", str(system))
    found = tools.checks("linux-x86_64", {"fake": tool(BINARY)}, isolated / ".tools", output=lambda *_: (1, ""))
    (check,) = [check for check in found if check.text.startswith("fake")]
    assert check.state is None
    assert check.line() == f"  - fake 1.0.0: not installed, {system / 'fake'} stands in  →  make install"


def test_run_fails_with_the_reason_when_the_tool_is_unavailable(isolated, capsys):
    assert tools.run(tool(BINARY), "darwin-arm64", [], isolated / ".tools") == 1
    assert "brew install fake" in capsys.readouterr().err


# The web head's app (ADR 0039): pnpm in group `web`, required only where there is a web/ app


def test_pnpm_is_pinned_in_the_web_group_that_make_install_installs():
    assert tools.load()["pnpm"].group == "web"
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "@$(TOOLS) install build web" in makefile
    assert "@$(TOOLS) install\n" in (REPO / "template" / "Makefile").read_text(encoding="utf-8")


def web_checks(isolated, *, app: bool, modules: bool):
    web_app = isolated / "web" / "package.json"
    if app:
        web_app.parent.mkdir()
        web_app.write_text("{}", encoding="utf-8")
    if modules:
        (web_app.parent / "node_modules").mkdir()
        (web_app.parent / "node_modules" / ".modules.yaml").write_text("", encoding="utf-8")
    pnpm = tool(BINARY, group="web")
    found = tools.checks(
        "linux-x86_64",
        {"pnpm": pnpm},
        isolated / ".tools",
        which=lambda name: f"/usr/bin/{name}",
        output=lambda *_: (0, "1.0"),
        web_app=web_app,
    )
    return [check for check in found if check.section == "Tools: web"]


def test_doctor_requires_the_web_tools_where_there_is_a_web_app(isolated):
    checks = web_checks(isolated, app=True, modules=False)
    assert [(check.state, check.fix) for check in checks] == [(False, "make install"), (False, "make install")]
    assert "web/node_modules" in checks[1].text


def test_doctor_passes_the_web_app_once_installed(isolated):
    checks = web_checks(isolated, app=True, modules=True)
    assert checks[1].state is True


def test_doctor_says_nothing_about_web_tools_in_a_plugin(isolated):
    assert web_checks(isolated, app=False, modules=False) == []
