"""Settings of the runner module."""

from typing import ClassVar, Literal

from pydantic import Field

from heinzel import ModuleSettings


class RunnerSettings(ModuleSettings):
    """Settings of the core agent runners: the `[system.runner]` table or `HZ_SYSTEM_RUNNER__*` variables.

    `[capabilities] agent_runner` picks the runner (`claude-code` by default, or `command`). What runs and with which
    tools is set in the user, site or `HZ_CONFIG` file or the environment only; a project `hz.toml` can't, because a
    repository someone else controls must not decide what runs on your machine.
    """

    section_title: ClassVar[str | None] = "Agent runner"  # the heading in Settings

    model: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]*$",  # a name, never an option: a project file may set it
        title="Model",
        description="The model every run uses; the agent's own default when not set.",
        examples=["sonnet"],
    )
    claude_executable: str = Field(
        default="claude",
        title="Claude Code CLI",
        description="The Claude Code CLI to run: a name found on your PATH, or the full path to it.",
        json_schema_extra={"x-expert": True},
    )
    read_only_tools: list[str] = Field(
        default=["Read", "Grep", "Glob"],
        title="Read-only tools",
        description="Claude Code tools a run may use; a profile asking for any other is refused. "
        "Add only tools that can't change anything.",
        json_schema_extra={"x-expert": True},
    )
    command: list[str] = Field(
        default_factory=list,
        title="Command line",
        description="Your own runner's command line, one argument per item, never run through a "
        "shell. Placeholders: {prompt_file}, {contract_file}, {output_file}, "
        "{working_dir}, {tools}, {model}, {timeout_seconds}, {max_usd} and "
        "{mcp_config}, a JSON file of MCP servers for a chat's tools. Double braces "
        "are literal braces.",
        examples=[["mytool", "ai", "run", "--prompt", "{prompt_file}"]],
        json_schema_extra={"x-expert": True},
    )
    command_output: Literal["stdout", "file"] = Field(
        default="stdout",
        title="Where the result is",
        description="Where your own runner's result is read from: what the command prints, or the output file.",
        json_schema_extra={
            "x-expert": True,
            "x-labels": {"stdout": "What the command prints", "file": "The output file"},
        },
    )
    command_format: Literal["text", "json"] = Field(
        default="text",
        title="Result format",
        description="How your own runner's result is read: as the draft itself, or as JSON with "
        "the draft under the draft key and, optionally, usage and total_cost_usd.",
        json_schema_extra={
            "x-expert": True,
            "x-labels": {"text": "Text: the draft itself", "json": "JSON: the draft under the draft key"},
        },
    )
    command_draft_key: str = Field(
        default="result",
        title="Draft key",
        description="The key of the draft in your own runner's JSON result.",
        json_schema_extra={"x-expert": True},
    )
    command_enforces_read_only: bool = Field(
        default=False,
        title="Command is read-only",
        description="Your own runner refuses to run until this is on. Turn it on once your "
        "command limits its agent to tools that can't change anything.",
        json_schema_extra={"x-expert": True},
    )
    command_read_only_tools: list[str] = Field(
        default_factory=list,
        title="Read-only tools of the command",
        description="Tools, as your command names them, that a profile may ask your own runner "
        "for; any other is refused.",
        json_schema_extra={"x-expert": True},
    )
    command_env: list[str] = Field(
        default_factory=list,
        title="Variables passed to the command",
        description="Environment variables your command gets besides PATH, HOME, LANG, LC_* and "
        "TMPDIR, such as the one holding its own sign-in. Heinzelmännchen's "
        "credential variables are never passed.",
        json_schema_extra={"x-expert": True},
    )

    ask_max_usd: float = Field(
        default=0.25,
        gt=0,
        allow_inf_nan=False,
        title="Most per answer",
        description="The most one answer, or one chat message, may cost in US dollars.",
        json_schema_extra={"x-unit": "usd"},
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset(
        {
            "ask_max_usd",
            "claude_executable",
            "read_only_tools",
            "command",
            "command_output",
            "command_format",
            "command_draft_key",
            "command_enforces_read_only",
            "command_read_only_tools",
            "command_env",
        }
    )
