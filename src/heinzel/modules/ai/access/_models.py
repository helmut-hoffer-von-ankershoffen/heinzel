"""Models and settings of the access module."""

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from heinzel import ModuleSettings

ADAPTER_PATTERN = r"^[a-z][a-z0-9-]*$"

AccessState = Literal["signed in", "not signed in", "not tested", "tested", "test failed", "not installed", "error"]
Use = Literal["chat", "turns", "coding"]


class AccessSettings(ModuleSettings):
    """The core's generic company gateway option, `gateway`: the `[ai.access]` table or `HZ_AI_ACCESS__*` variables.

    A company CLI that grants access to AI, described by data: a status command, a test command, the hosts it reaches
    and the agent runner that serves its chat. Commands are argument lists run without a shell, with PATH, HOME,
    LANG, LC_*, TMPDIR and the names in `gateway_env` only. A project `hz.toml` can set none of this, because a
    repository someone else controls must not decide what runs on your machine.
    """

    gateway_title: str = Field(
        default="Company AI gateway", max_length=64, description="What people pick the gateway by, e.g. `Acme AI`."
    )
    gateway_summary: str = Field(
        default="Your company's AI gateway CLI, set up in [ai.access].",
        max_length=200,
        description="One sentence on how the gateway reaches AI.",
    )
    gateway_status_command: list[str] = Field(
        default_factory=list[str],
        description="Says whether you are signed in without calling a model: exit code 0 when signed in, and "
        'optionally a JSON object on stdout, `{"signed_in": true, "models": ["..."], "detail": "..."}`; e.g. '
        '`["acme", "ai", "status", "--json"]`. Unset: the gateway isn\'t set up.',
    )
    gateway_test_command: list[str] = Field(
        default_factory=list[str],
        description="One cheap live call, run only by `hz ai access test --name gateway`: exit code 0 when it "
        "answered, and "
        'optionally a JSON object, `{"ok": true, "detail": "...", "model": "...", "cost_usd": 0.001}`; e.g. '
        '`["acme", "ai", "ping"]`.',
    )
    gateway_sign_in: str = Field(
        default="", max_length=200, description="The command that signs you in, shown as the fix, e.g. `acme login`."
    )
    gateway_runner: str | None = Field(
        default=None,
        pattern=ADAPTER_PATTERN,
        description="The agent runner that serves the gateway's chat and coding uses, e.g. `command` with "
        "`[system.runner] command` set to the gateway's agent CLI; `hz ai access use gateway` binds it.",
    )
    gateway_hosts: list[str] = Field(
        default_factory=list[str],
        description="Hosts the gateway sends prompts to, as host names or URLs, e.g. `ai.acme.example`; shown in the "
        "listing and the capability matrix.",
    )
    gateway_models: list[str] = Field(
        default_factory=list[str], description="Models the gateway offers, when its status command doesn't say."
    )
    gateway_env: list[str] = Field(
        default_factory=list[str],
        description="Environment variables passed to the gateway's commands besides PATH, HOME, LANG, LC_* and "
        "TMPDIR; HZ_CREDENTIAL_* variables are never passed.",
    )

    model: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._:/@-]*$",
        description="The model of the `pydantic-ai` option, as Pydantic AI names it, `<vendor>:<model>`, e.g. "
        "`anthropic:claude-sonnet-4-5`; its key is the credential `ai.access/api-key` or the vendor's variable.",
    )

    ignored_in_project: ClassVar[frozenset[str]] = frozenset(
        {
            "model",
            "gateway_title",
            "gateway_summary",
            "gateway_status_command",
            "gateway_test_command",
            "gateway_sign_in",
            "gateway_runner",
            "gateway_hosts",
            "gateway_models",
            "gateway_env",
        }
    )


class Bindings(ModuleSettings):
    """The two bindings AI access reads afresh on every call: `[capabilities] ai_access` and `agent_runner`.

    Read again on each listing, like the theme, because `hz ui` keeps running while a terminal switches.
    """

    ai_access: str | None = "claude-code"
    agent_runner: str | None = "claude-code"

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"ai_access", "agent_runner"})


class LastTest(BaseModel):
    """The last live check of an option, as `hz ai access test` kept it."""

    at: datetime = Field(description="When it ran, in UTC.")
    ok: bool = Field(description="Whether the option answered.")
    detail: str = Field(default="", description="The answer, or why it failed.")
    model: str | None = Field(default=None, description="The model that answered, when reported.")
    cost_usd: float | None = Field(default=None, description="What it cost in US dollars, when reported.")
    duration_seconds: float = Field(default=0.0, description="Wall-clock time of the check.")


class AccessOption(BaseModel):
    """One installed AI access option and what it says about itself."""

    name: str = Field(description="What `hz ai access use` takes, e.g. `claude-code`.")
    title: str = Field(description="What people pick it by, e.g. `Claude Code`.")
    summary: str = Field(description="One sentence on how it reaches AI.")
    module: str = Field(description="The qualified name of the module providing it, e.g. `system.runner`.")
    plugin: str | None = Field(description="The plugin it comes with, e.g. `heinzel-acme`; none when built in.")
    in_use: bool = Field(description="Whether it is the AI access in use.")
    status: AccessState = Field(
        description="`signed in` or `not signed in` as far as it tells offline; otherwise `tested` or `test failed` "
        "from the last `hz ai access test`, or `not tested`; `not installed` when what it needs is missing."
    )
    detail: str = Field(default="", description="What it found, e.g. the CLI's path.")
    fix: str = Field(default="", description="A command that makes it usable; often empty.")
    models: list[str] = Field(default_factory=list[str], description="Models it offers or is configured with.")
    runner: str | None = Field(default=None, description="The agent runner that serves its chat and coding uses.")
    hosts: list[str] = Field(default_factory=list[str], description="Hosts it sends prompts to, when known.")
    last_test: LastTest | None = Field(default=None, description="The last `hz ai access test` of it, if any.")


class AccessUse(BaseModel):
    """What one use of AI runs today."""

    use: Use = Field(description="`chat`, `turns` of Heinzelmännchen, or `coding` handoffs.")
    access: str | None = Field(description="The AI access it follows: the one in use.")
    runs: str = Field(description="What actually runs it today, e.g. `the agent runner claude-code`.")


class AccessList(BaseModel):
    """Every installed AI access option, the one in use, and what each use of AI runs."""

    selected: str | None = Field(description="The AI access in use, `[capabilities] ai_access`; none when unset.")
    options: list[AccessOption] = Field(description="Every installed option, by name.")
    uses: list[AccessUse] = Field(description="Chat, turns and coding handoffs, and what runs each today.")
    note: str | None = Field(default=None, description="Why something isn't as configured, e.g. a runner mismatch.")
    next: list[str] = Field(default_factory=list[str], description="What to run next.")


class AccessTestReport(BaseModel):
    """The outcome of `hz ai access test`, also kept for the listing."""

    name: str = Field(description="The option tested.")
    ok: bool = Field(description="Whether it answered.")
    detail: str = Field(default="", description="The answer, or why it failed.")
    fix: str = Field(default="", description="A command that fixes why it failed; often empty.")
    model: str | None = Field(default=None, description="The model that answered, when reported.")
    cost_usd: float | None = Field(default=None, description="What it cost in US dollars, when reported.")
    duration_seconds: float = Field(default=0.0, description="Wall-clock time of the check.")
