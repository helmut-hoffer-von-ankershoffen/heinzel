"""Settings of the runner module."""

from typing import Literal

from pydantic import Field

from heinzel import ModuleSettings


class RunnerSettings(ModuleSettings):
    """Settings of the core agent runners: the `[system.runner]` table or `HZ_SYSTEM_RUNNER__*` variables.

    `[capabilities] agent_runner` picks the runner (`claude-code` by default, or `command`).
    """

    model: str | None = Field(
        default=None, description="Model for every run, e.g. `sonnet`; the agent's own default when not set."
    )
    claude_executable: str = Field(default="claude", description="The Claude Code CLI, a name on PATH or a path.")
    read_only_tools: list[str] = Field(
        default=["Read", "Grep", "Glob"],
        description="Claude Code tools a run may use; a profile asking for anything else is refused. Add only tools "
        "that cannot change anything.",
    )
    command: list[str] = Field(
        default_factory=list,
        description="The `command` runner's command line, one argument per item, never through a shell. "
        "Placeholders: {prompt_file}, {contract_file}, {output_file}, {working_dir}, {tools}, {model}, "
        '{timeout_seconds}, {max_usd}; e.g. ["mytool", "ai", "run", "--prompt", "{prompt_file}"].',
    )
    command_output: Literal["stdout", "file"] = Field(
        default="stdout", description="Where the `command` runner reads the result: stdout, or {output_file}."
    )
    command_format: Literal["text", "json"] = Field(
        default="text",
        description="`text`: the output is the draft. `json`: an object with the draft under `command_draft_key` "
        "and, optionally, `usage` (input_tokens, output_tokens) and `total_cost_usd`.",
    )
    command_draft_key: str = Field(default="result", description="Key of the draft in `json` output.")
    command_enforces_read_only: bool = Field(
        default=False,
        description="Set to true once the configured command restricts its agent to read-only tools itself; until "
        "then doctor reports that the read-only profile is not enforced.",
    )
