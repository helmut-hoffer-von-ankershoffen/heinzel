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

    model: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]*$",  # a name, never an option: a project file may set it
        description="Model for every run, e.g. `sonnet`; the agent's own default when not set.",
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
        '{timeout_seconds}, {max_usd}; `{{` and `}}` are literal braces; e.g. ["mytool", "ai", "run", "--prompt", '
        '"{prompt_file}"].',
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
        description="The `command` runner refuses to run until this is true: set it once the configured command "
        "restricts its agent to read-only tools itself.",
    )
    command_read_only_tools: list[str] = Field(
        default_factory=list,
        description="Tools, in the command's naming, a tool profile may ask the `command` runner for; others "
        "are refused.",
    )
    command_env: list[str] = Field(
        default_factory=list,
        description="Environment variables passed to the command besides PATH, HOME, LANG, LC_* and TMPDIR, e.g. "
        "the variable that holds its own login; HZ_CREDENTIAL_* variables are never passed.",
    )

    ask_max_usd: float = Field(
        default=0.25,
        gt=0,
        allow_inf_nan=False,
        description="Most one answer of `hz system runner ask` (the web app's chat) may cost in US dollars.",
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
