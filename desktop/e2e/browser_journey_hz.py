"""The `hz` of the browser journey (ADR 0162): the real CLI, plus one agent runner whose model is a script.

Test-only. `browser-journey.mjs` puts a launcher for this file first on the PATH of an isolated Electron app, which
starts it as `hz ui serve`. Everything is the product's own code, except the model: the runner named `journey` runs
a Pydantic AI agent on a `FunctionModel` and calls hz's tools in-process (`direct_tools`, `pydantic_ai_tools`), as a
runner that keeps its model inside hz does. So the tool policy, the commit gate, the bridge and the desktop are real.

The script reads what the tools return (the tab id, the refs of the page) and decides the next call from that. Its
last answer is a JSON object of what each step returned, which the journey reads from the chat.
"""

import asyncio
import json
import os
import re
import time
from collections.abc import Callable, Generator
from datetime import timedelta
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import heinzel.kernel._boot as boot
from heinzel.kernel import AgentResult, Budget, Kernel, Module, ToolProfile, direct_tools, pydantic_ai_tools
from heinzel.kernel._kernel import ModuleRecord, Origin

SHOP = "https://" + os.environ.get("HZ_JOURNEY_SHOP", "shop.invalid")
SYNC = Path(os.environ.get("HZ_JOURNEY_SYNC", "."))
PREFIX = "mcp__hz__ui_browser_"
KEPT = 400
"""How much of each tool result goes into the report."""
NOT_PUBLIC = (
    "https://127.0.0.1/",
    "https://localhost/",
    "http://" + os.environ.get("HZ_JOURNEY_SHOP", "shop.invalid") + "/",
    "https://192.168.1.10/",
)
"""Addresses no agent may open: a literal address, a local name, plain HTTP and a private network."""

Step = tuple[str, dict[str, object]]
Script = Generator[Step, str, dict[str, object]]


def tab_of(result: str) -> str:
    """The tab id in a tool result, or empty."""
    found = re.search(r"t-[0-9a-f]{8}", result)
    return found.group(0) if found else ""


def ref_of(page: str, role: str, name: str) -> str:
    """The ref of the element with this role and name in a page as `ui_browser_read` returns it, or `ref_0`."""
    found = re.search(rf'- {role} \\?"{re.escape(name)}\\?"[^\[]{{0,120}}\[(ref_\d+)\]', page)
    return found.group(1) if found else "ref_0"


def first_turn() -> Script:
    """Open the shop, read it, try what is forbidden, do what is free, and ask for the one commit.

    Yields:
        The next tool and its arguments; each is answered with the tool's result.
    """
    report: dict[str, object] = {}
    answer = ""
    for _ in range(20):  # the desktop connects its bridge a moment after the service is up
        answer = yield "tabs", {}
        if '"available": true' in answer or '"available":true' in answer:
            break
        time.sleep(1)
    report["tabs"] = answer
    report["open_not_public"] = yield "open", {"url": NOT_PUBLIC[0]}
    tab = tab_of((yield "open", {"url": SHOP + "/"}))
    report["tab"] = tab
    page = yield "read", {"tab": tab}
    report["read"] = page
    refused = {}
    for address in NOT_PUBLIC:
        refused[address] = yield "navigate", {"tab": tab, "url": address}
    report["not_public"] = refused
    report["type_name"] = yield "type", {"tab": tab, "ref": ref_of(page, "textbox", "Name"), "text": "Mira"}
    report["click_note"] = yield "click", {"tab": tab, "ref": ref_of(page, "button", "Add a gift note")}
    password = ref_of(page, "textbox", "Password")
    report["type_password"] = yield "type", {"tab": tab, "ref": password, "text": "not-a-real-secret"}
    report["click_hover_swap"] = yield "click", {"tab": tab, "ref": ref_of(page, "button", "Show delivery times")}
    report["click_press_swap"] = yield "click", {"tab": tab, "ref": ref_of(page, "button", "Show opening hours")}
    card_tab = tab_of((yield "open", {"url": SHOP + "/card"}))
    card = yield "read", {"tab": card_tab}
    card_number = ref_of(card, "textbox", "Card number")
    report["type_card"] = yield "type", {"tab": card_tab, "ref": card_number, "text": "4111111111111111"}
    report["close_card"] = yield "close", {"tab": card_tab}
    page = yield "read", {"tab": tab}
    report["send"] = yield "click", {"tab": tab, "ref": ref_of(page, "button", "Send order")}
    report["after"] = yield "tabs", {}
    return report


def second_turn() -> Script:
    """Go on in the tab, wait until the person has pressed Stop, and try again.

    Yields:
        The next tool and its arguments; each is answered with the tool's result.
    """
    report: dict[str, object] = {}
    tab = tab_of((yield "tabs", {}))
    report["tab"] = tab
    page = yield "read", {"tab": tab}
    receipt = ref_of(page, "button", "Show receipt")
    report["click_before_stop"] = yield "click", {"tab": tab, "ref": receipt}
    (SYNC / "ready-for-stop").write_text("ready\n", encoding="utf-8")
    deadline = time.monotonic() + 90
    while not (SYNC / "stopped").exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    report["stop_seen"] = (SYNC / "stopped").exists()
    report["click_after_stop"] = yield "click", {"tab": tab, "ref": receipt}
    report["read_after_stop"] = yield "read", {"tab": tab}
    report["open_after_stop"] = yield "open", {"url": SHOP + "/"}
    report["tabs_after_stop"] = yield "tabs", {}
    return report


def shorten(value: object) -> object:
    """A report with every text cut, so it fits a chat answer."""
    if isinstance(value, dict):
        return {key: shorten(item) for key, item in value.items()}
    return value[:KEPT] if isinstance(value, str) else value


def scripted(script: Script) -> Callable[[list[ModelMessage], AgentInfo], ModelResponse]:
    """A model that calls one tool per answer, as the script says, and ends with the script's report as JSON."""
    started = False

    def model(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        nonlocal started
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        try:
            if started:
                last = returns[-1].content if returns else ""
                name, arguments = script.send(last if isinstance(last, str) else json.dumps(last, default=str))
            else:
                started = True
                name, arguments = next(script)
        except StopIteration as done:
            return ModelResponse(parts=[TextPart(json.dumps(shorten(done.value)))])
        return ModelResponse(parts=[ToolCallPart(PREFIX + name, dict(arguments))])

    return model


class JourneyRunner:
    """Runs a chat turn on the scripted model, with hz's tools called directly: no process and no MCP hop."""

    def __init__(self, kernel: Kernel) -> None:
        """Keep the kernel the tools are built from."""
        self._kernel = kernel

    def run(
        self, prompt: str, *, contract: str, tools: ToolProfile, timeout: timedelta, budget: Budget, **_choice: object
    ) -> AgentResult:
        """Answer `prompt`: the second scripted turn when the person's message asks for it, else the first."""
        del timeout, budget
        began = time.monotonic()
        rest, hz = direct_tools(self._kernel, tools)
        called: list[str] = []
        script = second_turn() if "JOURNEY-TURN-2" in prompt else first_turn()
        agent = Agent(
            FunctionModel(scripted(script)),
            instructions=contract,
            tools=pydantic_ai_tools(hz, counted=called.append),
        )
        answer = asyncio.run(agent.run(prompt))
        return AgentResult(
            runner="journey",
            status="completed",
            draft=answer.output,
            tool_calls={name: called.count(name) for name in called},
            duration_seconds=time.monotonic() - began,
            warnings=[f"hz pipe left out: {not rest.mcp_servers}"],
        )


module = Module("journey", "Test-only: the scripted agent runner of the browser journey.")
module.provides("agent-runner", modes={"read"}, name="journey")(JourneyRunner)


def main() -> None:
    """Add the runner's module to what the kernel discovers, then run the real CLI with this process's arguments."""
    discover = boot.discover

    def with_journey(**options: object) -> tuple[list[ModuleRecord], dict[str, str]]:
        records, errors = discover(**options)  # type: ignore[arg-type]  # passed on as given
        origin = Origin(distribution="heinzel-browser-journey", version="0", builtin=False)
        records.append(ModuleRecord(qualname="journey", module=module, origin=origin))
        return records, errors

    boot.discover = with_journey  # type: ignore[assignment]  # the test's one seam
    from heinzel.heads.cli import main as cli  # noqa: PLC0415 - after the seam is in place

    cli()


if __name__ == "__main__":
    main()
