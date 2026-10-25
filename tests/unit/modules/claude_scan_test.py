"""Scanning Claude's configuration in fake home directories: what is found, that no secret value survives, and that
links leaving their directory are not followed (ADR 0062)."""

import json
import os
from pathlib import Path

import pytest

from heinzel.modules.system.claude import ClaudeService
from heinzel.modules.system.claude._scan import Refused, _read

pytestmark = pytest.mark.unit

SECRETS = ("sk-live-SECRET-1", "bearer-SECRET-2", "keychain-SECRET-3", "arg-SECRET-4", "query-SECRET-5")


def write(path: Path, content: str | dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return path


def skill(directory: Path, name: str, description: str = "Does a thing.", extra: str = "") -> Path:
    write(directory / name / "SKILL.md", f"---\nname: {name}\ndescription: {description}\n{extra}---\nBody.\n")
    return directory / name


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A home with every source Claude has: Desktop, an extension, user, local and project scope, a plugin."""
    root = tmp_path / "home"
    project = root / "Code" / "demo"
    project.mkdir(parents=True)
    write(
        root / "Library/Application Support/Claude/claude_desktop_config.json",
        {"mcpServers": {"notes": {"command": "/usr/bin/npx", "args": ["-y", "notes-mcp", SECRETS[3]]}}},
    )
    write(
        root / "Library/Application Support/Claude/Claude Extensions/local.mcpb.acme.maps/manifest.json",
        {
            "name": "maps",
            "user_config": {"api_key": {"sensitive": True}, "region": {"sensitive": False}},
            "server": {
                "type": "node",
                "mcp_config": {
                    "command": "node",
                    "args": ["${__dirname}/server.js"],
                    "env": {"MAPS_KEY": "${user_config.api_key}", "REGION": "${user_config.region}"},
                },
            },
        },
    )
    write(
        root / ".claude.json",
        {
            "mcpServers": {
                "tickets": {
                    "type": "http",
                    "url": f"https://user:pw@tickets.example.com:8443/mcp?token={SECRETS[4]}",
                    "headers": {"Authorization": f"Bearer {SECRETS[1]}"},
                },
                "legacy": {"type": "sse", "url": "https://sse.example.com/events"},
                "mine": {"command": "uvx", "args": ["--from", "heinzel", "hz", "system", "serve", "mcp"]},
            },
            "claudeAiMcpEverConnected": ["a", "b", "c"],
            "projects": {
                str(project): {"mcpServers": {"local-db": {"command": "npx", "env": {"DB_URL": SECRETS[0]}}}},
                "/does/not/exist": {},
            },
        },
    )
    write(project / ".mcp.json", {"mcpServers": {"shared": {"command": "bun", "env": {"TOKEN": "${TOKEN}"}}}})
    skill(project / ".claude/skills", "release", extra="allowed-tools: Bash\n")
    skill(root / ".claude/skills", "review", "Reviews code.\n  Second line.")
    write(root / ".claude/settings.json", {"enabledPlugins": {"tools@market": True, "off@market": False}})
    plugins = root / ".claude/plugins"
    for name in ("tools", "off"):
        base = plugins / "cache" / name
        write(base / ".mcp.json", {name + "-srv": {"command": "uvx", "args": [name]}})
        skill(base / "skills", name + "-skill")
    write(
        plugins / "installed_plugins.json",
        {
            "version": 2,
            "plugins": {
                "tools@market": [{"scope": "user", "installPath": str(plugins / "cache" / "tools")}],
                "off@market": [{"scope": "user", "installPath": str(plugins / "cache" / "off")}],
            },
        },
    )
    return root


def test_finds_every_source(home: Path):
    scan = ClaudeService(home).scan()
    servers = {s.id.replace(str(home), "~"): s for s in scan.servers}
    assert set(servers) == {
        "desktop:notes",
        "desktop-extension:maps",
        "code-user:tickets",
        "code-user:legacy",
        "code-user:mine",
        "code-local:~/Code/demo:local-db",
        "code-project:~/Code/demo:shared",
        "code-plugin:tools@market:tools-srv",
    }
    assert servers["code-user:tickets"].transport == "http"
    assert servers["code-user:legacy"].transport == "sse"
    assert servers["desktop:notes"].command == "npx"
    assert servers["desktop:notes"].arguments == 3
    assert {s.id for s in scan.skills} == {
        "code-user:review",
        "code-project:~/Code/demo:release",
        "code-plugin:tools@market:tools-skill",
    }
    assert scan.connectors == 3
    assert scan.skipped == []


def test_describes_auth_by_names_only(home: Path):
    by_name = {s.name: s for s in ClaudeService(home).scan().servers}
    assert by_name["tickets"].auth == "header"
    assert by_name["tickets"].header_keys == ["Authorization"]
    assert by_name["tickets"].host == "https://tickets.example.com:8443"
    assert by_name["legacy"].auth == "none-or-oauth"
    assert by_name["local-db"].auth == "env"
    assert by_name["local-db"].env_keys == ["DB_URL"]
    assert by_name["maps"].auth == "keychain"
    assert by_name["maps"].env_keys == ["MAPS_KEY", "REGION"]
    assert by_name["notes"].auth == "none"


def test_a_token_in_the_url_is_named_as_such(home: Path):
    write(home / ".claude.json", {"mcpServers": {"hooked": {"url": f"https://h.example.com/mcp?key={SECRETS[4]}"}}})
    (server,) = [s for s in ClaudeService(home).scan().servers if s.name == "hooked"]
    assert server.auth == "url"
    assert SECRETS[4] not in server.model_dump_json()


def test_no_secret_value_is_in_any_result(home: Path):
    service = ClaudeService(home)
    text = service.scan().model_dump_json() + service.summary().model_dump_json()
    for secret in SECRETS:
        assert secret not in text
    assert "user:pw" not in text
    assert "/mcp" not in text


def test_the_fingerprint_holds_no_argument_or_url_text(home: Path):
    def fingerprint() -> str:
        return {s.name: s for s in ClaudeService(home).scan().servers}["notes"].fingerprint

    before = fingerprint()
    config_path = home / "Library/Application Support/Claude/claude_desktop_config.json"
    config = json.loads(config_path.read_text())
    config["mcpServers"]["notes"]["args"][2] = "another-secret"  # same shape: not noticed in slice 1
    write(config_path, config)
    assert fingerprint() == before
    config["mcpServers"]["notes"]["args"].append("--extra")
    write(config_path, config)
    assert fingerprint() != before


def test_fingerprint_follows_the_entry_not_the_secret(home: Path):
    def fingerprint() -> str:
        return {s.name: s for s in ClaudeService(home).scan().servers}["tickets"].fingerprint

    before = fingerprint()
    config = json.loads((home / ".claude.json").read_text())
    config["mcpServers"]["tickets"]["headers"]["Authorization"] = "Bearer rotated"
    write(home / ".claude.json", config)
    assert fingerprint() == before
    config["mcpServers"]["tickets"]["headers"]["X-New"] = "1"
    write(home / ".claude.json", config)
    assert fingerprint() != before


def test_recognises_hz_itself(home: Path):
    by_name = {s.name: s for s in ClaudeService(home).scan().servers}
    assert by_name["mine"].status == "self"
    assert by_name["notes"].status == "found"


def test_only_enabled_plugins_count(home: Path):
    scan = ClaudeService(home).scan()
    assert not [s for s in scan.servers if s.origin == "off"]
    assert not [s for s in scan.skills if s.origin == "off"]


def test_skill_frontmatter_and_bundled_files(home: Path):
    release = home / "Code/demo/.claude/skills/release"
    write(release / "scripts/run.sh", "echo secret-ish")
    write(release / "references/a.md", "a")
    write(release / "references/b.md", "b")
    found = {s.name: s for s in ClaudeService(home).scan().skills}
    assert found["release"].files == {"scripts": 1, "references": 2}
    assert found["release"].frontmatter_keys == ["allowed-tools", "description", "name"]
    assert found["release"].path == "~/Code/demo/.claude/skills/release"
    assert found["review"].description == "Reviews code. Second line."


def test_skill_text_is_cleaned_and_cut(home: Path):
    write(home / ".claude/skills/evil/SKILL.md", '---\nname: evil\ndescription: "hi\\u202e\\U000e0041" \n---\n')
    skill(home / ".claude/skills", "long", "x" * 1000)
    found = {s.name: s for s in ClaudeService(home).scan().skills}
    assert found["evil"].description == "hi"
    assert len(found["long"].description) == 401


def test_a_skill_directory_linked_outside_is_not_followed(home: Path, tmp_path: Path):
    outside = skill(tmp_path / "elsewhere", "stolen", "Outside the skills directory.")
    (home / ".claude/skills/stolen").symlink_to(outside)
    inside = skill(home / ".claude/skills", "real")
    (home / ".claude/skills/alias").symlink_to(inside)
    scan = ClaudeService(home).scan()
    assert "stolen" not in {s.name for s in scan.skills}
    assert [s.reason for s in scan.skipped if s.path.endswith("stolen")] == ["a link leaves its directory"]
    assert "real" in {s.name for s in scan.skills}


def test_a_linked_skill_file_and_bundled_files_are_not_followed(home: Path, tmp_path: Path):
    target = write(tmp_path / "private.txt", "TOP-SECRET")
    (home / ".claude/skills/review/SKILL.md").unlink()
    (home / ".claude/skills/review/SKILL.md").symlink_to(target)
    (home / ".claude/skills/real").mkdir()
    write(home / ".claude/skills/real/SKILL.md", "---\nname: real\n---\n")
    (home / ".claude/skills/real/scripts").symlink_to(tmp_path)
    scan = ClaudeService(home).scan()
    assert [s.reason for s in scan.skipped if s.path.endswith("review")] == ["a link leaves its directory"]
    assert {s.name: s for s in scan.skills}["real"].files == {}
    assert "TOP-SECRET" not in scan.model_dump_json()


def test_a_linked_skills_directory_leaving_the_project_is_skipped(home: Path, tmp_path: Path):
    skill(tmp_path / "shared-skills", "foreign")
    project_skills = home / "Code/demo/.claude/skills"
    for entry in project_skills.iterdir():
        for inner in entry.iterdir():
            inner.unlink()
        entry.rmdir()
    project_skills.rmdir()
    project_skills.symlink_to(tmp_path / "shared-skills")
    scan = ClaudeService(home).scan()
    assert "foreign" not in {s.name for s in scan.skills}
    assert any(s.path.endswith(".claude/skills") for s in scan.skipped)


def test_a_config_file_linked_outside_the_home_is_not_read(home: Path, tmp_path: Path):
    outside = write(tmp_path / "other.json", {"mcpServers": {"intruder": {"command": "x"}}})
    config = home / "Library/Application Support/Claude/claude_desktop_config.json"
    config.unlink()
    config.symlink_to(outside)
    scan = ClaudeService(home).scan()
    assert "intruder" not in {s.name for s in scan.servers}
    assert [s.reason for s in scan.skipped] == ["a link leaves its directory"]


def test_a_config_file_linked_inside_the_home_is_read(home: Path):
    dotfiles = write(home / "dotfiles/claude.json", {"mcpServers": {"kept": {"command": "x"}}})
    config = home / ".claude.json"
    config.unlink()
    config.symlink_to(dotfiles)
    assert "kept" in {s.name for s in ClaudeService(home).scan().servers}


def test_a_plugin_install_path_outside_the_plugins_directory_is_refused(home: Path, tmp_path: Path):
    write(tmp_path / "evil/.mcp.json", {"evil-srv": {"command": "x"}})
    write(
        home / ".claude/plugins/installed_plugins.json",
        {"plugins": {"tools@market": [{"scope": "user", "installPath": str(tmp_path / "evil")}]}},
    )
    scan = ClaudeService(home).scan()
    assert "evil-srv" not in {s.name for s in scan.servers}
    assert scan.skipped


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", ""])
def test_bad_files_are_skipped_with_a_reason_and_no_content(home: Path, content: str):
    write(home / ".claude.json", content)
    scan = ClaudeService(home).scan()
    assert [s.path for s in scan.skipped] == ["~/.claude.json"]
    assert "desktop:notes" in {s.id for s in scan.servers}


def test_a_file_that_is_too_large_is_not_read(tmp_path: Path):
    big = write(tmp_path / "big.json", "x" * 100)
    with pytest.raises(Refused, match="too large"):
        _read(big, tmp_path, 10)


def test_a_missing_home_finds_nothing(tmp_path: Path):
    scan = ClaudeService(tmp_path / "nobody").scan()
    assert scan.servers == scan.skills == scan.skipped == []
    assert scan.next


def test_the_summary_counts_without_names(home: Path):
    summary = ClaudeService(home).summary()
    assert summary.servers["code-user"] == 3
    assert summary.skills["code-user"] == 1
    assert summary.connectors == 3
    assert "tickets" not in summary.model_dump_json()


def test_health_is_up_when_everything_reads_and_degraded_otherwise(home: Path):
    service = ClaudeService(home)
    assert service.checks()[0].status.value == "up"
    write(home / ".claude.json", "{broken")
    check = service.checks()[0]
    assert check.status.value == "degraded"
    assert ".claude.json" not in check.detail


def test_only_the_user_scope_install_of_a_plugin_counts_and_only_the_latest(home: Path):
    plugins = home / ".claude/plugins"
    old = plugins / "cache/tools-old"
    write(old / ".mcp.json", {"stale": {"command": "x"}})
    new = plugins / "cache/tools"
    installed = {
        "plugins": {
            "tools@market": [
                {"scope": "user", "installPath": str(old)},
                {"scope": "project", "installPath": str(plugins / "cache/off")},
                {"scope": "user", "installPath": str(new)},
            ]
        }
    }
    write(plugins / "installed_plugins.json", installed)
    names = {s.name for s in ClaudeService(home).scan().servers}
    assert "tools-srv" in names
    assert not names & {"stale", "off-srv"}


def test_a_project_that_is_the_home_directory_does_not_repeat_the_user_sources(home: Path):
    config = json.loads((home / ".claude.json").read_text())
    config["projects"][str(home)] = {}
    write(home / ".claude.json", config)
    write(home / ".mcp.json", {"mcpServers": {"home-shared": {"command": "x"}}})
    scan = ClaudeService(home).scan()
    assert [s.id for s in scan.skills].count("code-user:review") == 1
    assert not [s for s in scan.skills if s.source == "code-project" and s.origin == "~/."]
    assert "home-shared" not in {s.name for s in scan.servers}


def test_a_path_inside_an_argument_does_not_make_a_server_hz(home: Path):
    write(
        home / ".claude.json",
        {"mcpServers": {"files": {"command": "npx", "args": ["server-filesystem", "/Users/me/Code/heinzel"]}}},
    )
    (server,) = [s for s in ClaudeService(home).scan().servers if s.name == "files"]
    assert server.status == "found"


def test_a_named_pipe_is_refused_not_opened(tmp_path: Path):
    pipe = tmp_path / "config.json"
    os.mkfifo(pipe)
    with pytest.raises(Refused, match="not a regular file"):
        _read(pipe, tmp_path, 100)


@pytest.mark.parametrize(
    "frontmatter",
    ["name: x\ndate: 2024-13-45", "a: &a [1, 2]\nb: [*a, *a]", "name: [unclosed"],
)
def test_frontmatter_that_cannot_be_parsed_safely_costs_the_skill_its_metadata_not_the_scan(
    home: Path, frontmatter: str
):
    write(home / ".claude/skills/odd/SKILL.md", f"---\n{frontmatter}\n---\nBody")
    found = {s.name: s for s in ClaudeService(home).scan().skills}
    assert found["odd"].description == ""
    assert "review" in found


def test_a_directory_without_a_skill_file_is_not_an_error(home: Path):
    (home / ".claude/skills/notes").mkdir()
    assert ClaudeService(home).scan().skipped == []


def test_a_refused_link_is_listed_but_does_not_make_doctor_complain(home: Path, tmp_path: Path):
    outside = skill(tmp_path / "elsewhere", "dotfile-skill")
    (home / ".claude/skills/dotfile-skill").symlink_to(outside)
    service = ClaudeService(home)
    assert service.scan().skipped
    assert service.checks()[0].status.value == "up"
