"""Skills for MCP clients (ADR 0061): prompts and resources on stdio only, never over HTTP or in a chat."""

import anyio
import pytest
from mcp import Client

from heinzel import HeinzelError, Module, Skills
from heinzel.heads import mcp

pytestmark = pytest.mark.unit


def notes(tmp_path) -> Module:
    directory = tmp_path / "review-notes"
    (directory / "references").mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: review-notes\ndescription: Review notes.\n---\n\nDo X.\n")
    (directory / "references" / "list.md").write_text("- intent\n")
    module = Module("notes", "Notes.")
    module.skill(directory)
    return module


def test_stdio_serves_each_skill_as_a_prompt_and_its_files_as_resources(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True, notes=notes(tmp_path))

    async def read() -> tuple[set[str], set[str], str, str]:
        async with Client(mcp.build(kernel)) as client:
            prompts = {p.name for p in (await client.list_prompts()).prompts}
            resources = {str(r.uri) for r in (await client.list_resources()).resources}
            prompt = await client.get_prompt("skill_review_notes")
            content = await client.read_resource("skill://review-notes/references/list.md")
            return prompts, resources, prompt.messages[0].content.text, content.contents[0].text

    prompts, resources, prompt, content = anyio.run(read)
    assert {"skill_review_notes", "skill_explain_heinzel"} <= prompts
    assert {"skill://review-notes/SKILL.md", "skill://review-notes/references/list.md"} <= resources
    assert prompt.startswith("Skill `review-notes` from notes (plugin")
    assert content == "- intent\n"


@pytest.mark.parametrize(("transport", "chat"), [("streamable-http", None), ("stdio", "0123456789abcdef")])
def test_http_and_chat_servers_offer_no_skills(make_kernel, tmp_path, transport, chat):
    kernel = make_kernel(builtins=True, notes=notes(tmp_path))

    async def listed() -> tuple[int, int]:
        async with Client(mcp.build(kernel, transport, chat=chat)) as client:
            return len((await client.list_prompts()).prompts), len((await client.list_resources()).resources)

    assert anyio.run(listed) == (0, 0)


def test_a_file_changed_since_import_is_a_resource_error(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True, notes=notes(tmp_path))
    (tmp_path / "review-notes" / "references" / "list.md").write_text("- swapped\n")

    async def read() -> str:
        async with Client(mcp.build(kernel)) as client:
            try:
                await client.read_resource("skill://review-notes/references/list.md")
            except Exception as exc:  # noqa: BLE001 - whatever the client raises for an error result
                return str(exc)
            return "read"

    assert "changed since hz started" in anyio.run(read)


@pytest.mark.parametrize("chat", [None, "0123456789abcdef"])
def test_no_mcp_tool_lists_or_shows_skills_so_a_chat_reads_only_its_own(make_kernel, tmp_path, chat):
    kernel = make_kernel(builtins=True, notes=notes(tmp_path))

    async def tools() -> set[str]:
        async with Client(mcp.build(kernel, chat=chat)) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    assert not {name for name in anyio.run(tools) if name.startswith("system_skill")}


def test_a_skill_gone_after_start_is_a_prompt_error(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True, notes=notes(tmp_path))
    server = mcp.build(kernel)
    kernel.resolve(Skills).load = lambda *a, **k: (_ for _ in ()).throw(HeinzelError("gone"))  # type: ignore[method-assign]

    async def get() -> str:
        async with Client(server) as client:
            try:
                await client.get_prompt("skill_review_notes")
            except Exception as exc:  # noqa: BLE001 - whatever the client raises for an error result
                return str(exc)
            return "read"

    assert anyio.run(get) != "read"  # an error result, never a body
