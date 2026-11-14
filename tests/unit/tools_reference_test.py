"""`hz system tools` and the generated references share one renderer, and the committed Markdown matches it."""

import json
import re
from pathlib import Path

import pytest
import yaml

from heinzel import Config, HeinzelError, Kernel, Settings
from heinzel.kernel import discover
from heinzel.modules.system._tools import mcp_reference, render, served_mcp_tools, tools

pytestmark = pytest.mark.unit

DOCS = Path(__file__).resolve().parents[2] / "docs"


@pytest.fixture(scope="module")
def built_ins() -> Kernel:
    config = Config.empty()
    return Kernel(config.settings(Settings, "hz"), discover(plugins=False)[0], config=config)


def test_every_format_renders_the_same_operations(built_ins):
    entries = tools(built_ins, modules=["system.config"])
    assert {e.module for e in entries} == {"system.config"}
    names = [e.name for e in entries]
    assert "show" in names
    assert [d["name"] for d in yaml.safe_load(render(entries, "yaml"))] == names
    assert [d["name"] for d in json.loads(render(entries, "json"))] == names
    assert "CLI:  hz system config show" in render(entries, "text")
    assert "### `system.config` show" in render(entries, "markdown")


def test_head_columns_say_where_an_operation_runs(built_ins):
    by_name = {(e.module, e.name): e for e in tools(built_ins)}
    show = by_name["system.config", "show"]
    assert show.heads.cli == "hz system config show"
    assert show.heads.mcp == "system_config_show"
    assert show.heads.rest == "GET /system/config/show"
    assert show.heads.web == "GET /api/system/config/show"
    init = by_name["system.config", "init"]
    assert init.heads.rest == "POST /system/config/init"
    assert not init.read_only
    # An operation only the web app reaches has no CLI command, no MCP tool and no REST endpoint.
    web_only = [e for e in tools(built_ins) if e.heads.web and not (e.heads.cli or e.heads.mcp or e.heads.rest)]
    assert web_only


def test_head_filter_keeps_only_that_heads_operations(built_ins):
    mcp = tools(built_ins, head="mcp")
    assert mcp
    assert all(e.heads.mcp for e in mcp)
    data = yaml.safe_load(render(mcp, "yaml", "mcp"))
    assert all(set(d["heads"]) == {"mcp"} and d["input_schema"]["type"] == "object" for d in data)
    assert all(e.heads.cli for e in tools(built_ins, head="cli"))
    assert all(e.heads.rest for e in tools(built_ins, head="rest"))


def test_parameters_carry_type_default_and_description(built_ins):
    init = next(e for e in tools(built_ins, modules=["system.config"]) if e.name == "init")
    force = next(p for p in init.parameters if p.name == "force")
    assert (force.type, force.required, force.default) == ("boolean", False, False)
    assert force.description
    assert init.result == "string"


def test_the_operation_runs_through_the_kernel(built_ins):
    from heinzel.modules.system import tools as operation

    result = operation(built_ins, output="yaml", head="all", module=["system.config"])
    assert result.format == "yaml"
    assert result.count == len(yaml.safe_load(result.text))


def test_committed_mcp_reference_is_current(built_ins):
    served = served_mcp_tools(built_ins)
    text = mcp_reference(tools(built_ins, head="mcp"), served)
    assert (DOCS / "MCP_REFERENCE.md").read_text(encoding="utf-8") == text


def test_the_mcp_reference_is_what_the_served_server_lists(built_ins):
    served = served_mcp_tools(built_ins)
    text = mcp_reference(tools(built_ins, head="mcp"), served)
    assert [m for m in re.findall(r"^## `([^`]+)`", text, re.MULTILINE) if m not in served] == []
    assert re.findall(r"^## `([^`]+)`", text, re.MULTILINE) == list(served)
    assert "system_runner_chat_confirm_authorized_mcp" not in served  # chat only, so not in the reference
    assert "system_runner_chat_confirm_authorized_mcp" not in text
    assert "approval_proof" not in json.dumps(served["heinzel_schedule_control"].input_schema)
    assert "approval_proof" not in text.split("## `heinzel_schedule_control`")[1].split("\n## `")[0]
    deprecated = [n for n, t in served.items() if (t.description or "").startswith("Deprecated")]
    assert deprecated
    assert all(f"## `{n}`" in text for n in deprecated)


def test_an_unknown_module_is_an_error(built_ins):
    with pytest.raises(HeinzelError, match="nosuch"):
        tools(built_ins, modules=["nosuch"])


def test_the_web_head_can_be_asked_for(built_ins):
    web = tools(built_ins, head="web")
    assert web
    assert all(e.heads.web for e in web)


def test_the_mcp_reference_lists_each_tool_with_its_input_schema(built_ins):
    text = mcp_reference(tools(built_ins, head="mcp"), served_mcp_tools(built_ins))
    assert "## `system_config_show`" in text
    assert '"properties"' in text


def test_generate_docs_check_finds_nothing_stale_including_the_references():
    import importlib.util
    import subprocess
    import sys

    root = DOCS.parent
    env = {k: v for k, v in __import__("os").environ.items() if k != "HZ_SYSTEM_AGENT__SKILL_DIRS"}
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "generate_docs.py"), "--check"],
        capture_output=True, text=True, cwd=root, env=env, timeout=240, check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert importlib.util.find_spec("heinzel")


def test_a_pull_request_does_not_check_the_references(tmp_path):
    import sys

    sys.path.insert(0, str(DOCS.parent))
    try:
        from scripts import generate_docs
    finally:
        sys.path.remove(str(DOCS.parent))
    assert set(generate_docs.REFERENCE_FILES) == {
        "MODULES.md",
        "CLI_REFERENCE.md",
        "MCP_REFERENCE.md",
        "CONFIGURATION.md",
    }
