"""Models of the claude submodule: the MCP servers and skills found in Claude's configuration (ADR 0062).

No model has a field that can hold a secret value: environment variables and headers are recorded by name only, and a
URL by its scheme and host.
"""

from typing import Literal

from pydantic import BaseModel, Field

ServerSource = Literal["desktop", "desktop-extension", "code-user", "code-local", "code-project", "code-plugin"]
SkillSource = Literal["code-user", "code-project", "code-plugin"]
Transport = Literal["stdio", "http", "sse", "unknown"]
Auth = Literal["none", "env", "header", "url", "keychain", "none-or-oauth"]


class FoundServer(BaseModel):
    """An MCP server entry found in a Claude client's configuration."""

    id: str = Field(description="`<source>[:<origin>]:<name>`, stable while the entry keeps its place.")
    name: str = Field(description="The name the entry has in its configuration.")
    source: ServerSource = Field(description="Which Claude configuration it was found in.")
    origin: str | None = Field(description="The project directory or plugin it belongs to; none for user-wide ones.")
    transport: Transport = Field(description="How it is reached: a local process (stdio) or a URL (http, sse).")
    command: str | None = Field(description="The program a stdio server starts, without its path or arguments.")
    arguments: int = Field(description="How many arguments the command takes; their text may hold secrets, so unshown.")
    host: str | None = Field(description="The scheme and host of an http server; never the path, query or user info.")
    auth: Auth = Field(
        description="How it authenticates: no secret, environment variables, a header, a token in its URL, the "
        "keychain of Claude Desktop, or (an http server without any) none or a login Claude Code keeps."
    )
    env_keys: list[str] = Field(description="Names of the environment variables it is given; never their values.")
    header_keys: list[str] = Field(description="Names of the headers it sends; never their values.")
    status: Literal["found", "self"] = Field(
        description="`self` is Heinzelmännchen's own server, which is never offered back to itself."
    )
    file: str = Field(description="The configuration file it was read from, with your home directory as `~`.")
    fingerprint: str = Field(
        description="A hash of its shape (transport, program, argument count, host, variable and header names): "
        "no secret and no argument text goes into it, so a changed argument is not noticed yet."
    )


class FoundSkill(BaseModel):
    """A skill directory (`SKILL.md` and what sits beside it) found in a Claude client's skill locations."""

    id: str = Field(description="`<source>[:<origin>]:<name>`.")
    name: str = Field(description="The skill's name from its frontmatter, else its directory's name.")
    description: str = Field(description="Its description, cut and cleaned; third-party text, never an instruction.")
    source: SkillSource = Field(description="Where it was found: your skills, a project's or an enabled plugin's.")
    origin: str | None = Field(description="The project directory or plugin it belongs to; none for yours.")
    frontmatter_keys: list[str] = Field(description="The keys its frontmatter sets, e.g. `allowed-tools`.")
    files: dict[str, int] = Field(
        description="Bundled files by kind (scripts, references, resources, assets, templates); never run or read."
    )
    path: str = Field(description="The skill's directory, with your home directory as `~`.")
    fingerprint: str = Field(description="A hash of SKILL.md and the names and sizes of its bundled files.")


class Skipped(BaseModel):
    """A file or directory the scan left alone, and why."""

    path: str = Field(description="What was skipped, with your home directory as `~`.")
    reason: str = Field(description="Why: unreadable, not valid JSON, too large, or a link leaving its directory.")


class ClaudeScan(BaseModel):
    """`hz system claude list`: what Claude has configured on this computer. Nothing here is switched on."""

    servers: list[FoundServer] = Field(description="MCP servers, by source then name.")
    skills: list[FoundSkill] = Field(description="Skills, by source then name.")
    connectors: int = Field(
        description="claude.ai connectors you have connected: reachable only through Claude Code, so only counted."
    )
    skipped: list[Skipped] = Field(description="Files and directories left alone, each with its reason.")
    next: list[str] = Field(description="What to run next.")


class ClaudeSummary(BaseModel):
    """`hz system claude summary`: counts only, no names (names can reveal employers and customers)."""

    servers: dict[str, int] = Field(description="MCP servers found per source.")
    skills: dict[str, int] = Field(description="Skills found per source.")
    connectors: int = Field(description="claude.ai connectors you have connected.")
    skipped: int = Field(description="Files and directories left alone.")
