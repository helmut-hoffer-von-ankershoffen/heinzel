"""Skills for MCP clients (ADR 0060): each skill a prompt, each of its files a resource, read when asked for.

Only on the stdio server a client started as you, never over streamable HTTP (no authentication) and never in a
chat's server: a company plugin's skill can be internal. Everything is read through the kernel's `Skills` service, so
the same checks apply as for a turn: cleaned text, only files the skill lists, nothing changed since import. The list
is made when the server starts; a skill added later appears once the client restarts the server.
"""

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError
from mcp.server.mcpserver.prompts.base import Prompt
from mcp.server.mcpserver.resources.types import FunctionResource

from heinzel.kernel import HeinzelError, Kernel, SkillEntry, Skills, clean_text, skill_frame

SCHEME = "skill"
SKILL_FILE = "SKILL.md"


def add_skills(server: MCPServer, kernel: Kernel) -> None:
    """Add a prompt `skill_<name>` per skill and a resource `skill://<name>/<path>` per file, `SKILL.md` too."""
    if not kernel.provides(Skills):
        return
    skills = kernel.resolve(Skills)
    for entry in skills.index():
        server.add_prompt(_prompt(skills, entry))
        for path in (SKILL_FILE, *entry.files):
            server.add_resource(_resource(skills, entry, path))


def _prompt(skills: Skills, entry: SkillEntry) -> Prompt:
    def render() -> str:
        try:
            return skill_frame(skills.load(entry.name))
        except HeinzelError as exc:
            raise ValueError(str(exc)) from exc

    return Prompt.from_function(
        render,
        name=f"skill_{entry.name.replace('-', '_')}",
        title=entry.name,
        description=clean_text(entry.description),
    )


def _resource(skills: Skills, entry: SkillEntry, path: str) -> FunctionResource:
    def read() -> str:
        try:
            return skills.load(entry.name).body if path == SKILL_FILE else skills.read_file(entry.name, path)
        except HeinzelError as exc:
            raise ResourceError(str(exc)) from exc

    return FunctionResource.from_function(
        read,
        uri=f"{SCHEME}://{entry.name}/{path}",
        name=f"{entry.name}/{path}",
        description=clean_text(entry.description) if path == SKILL_FILE else f"A file of the skill {entry.name}.",
        mime_type="text/markdown" if path.endswith(".md") else "text/plain",
    )
