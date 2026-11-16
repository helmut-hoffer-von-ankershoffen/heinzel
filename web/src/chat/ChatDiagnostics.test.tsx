import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ChatDiagnostics, diagnosticsEvent } from "./ChatDiagnostics";

const event = {
  preparation_ms: 23,
  discovery_ms: 3,
  provider_ms: 420,
  requests: 1,
  context: { messages: 5, input_tokens: 200, window_tokens: 1000 },
  servers: [
    {
      name: "Internal",
      internal: true,
      offered: true,
      tool_count: 1,
      tools: [{ name: "system_doctor", description: "Check health" }],
    },
    {
      name: "weather",
      internal: false,
      offered: true,
      tool_count: 1,
      tools: [{ name: "forecast", description: "Get a forecast" }],
    },
  ],
};

it("shows measured timings, context and the offered tools of each server", () => {
  const snapshot = diagnosticsEvent(event);
  expect(snapshot).not.toBeNull();
  if (!snapshot) throw new Error("Expected diagnostics");
  render(<ChatDiagnostics turns={[{ id: "one", snapshot }]} onClose={vi.fn()} />);
  expect(screen.getByText("Preparation: 23 ms")).toBeInTheDocument();
  expect(screen.getByText("Provider requests: 420 ms (1)")).toBeInTheDocument();
  expect(screen.getByText("Input tokens: 200 / 1000")).toBeInTheDocument();
  expect(screen.getByText("Offered servers · 2")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Internal/ }));
  expect(screen.getByText("system_doctor")).toBeInTheDocument();
  expect(screen.getByText("Check health")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  fireEvent.click(screen.getByRole("button", { name: /weather/ }));
  expect(screen.getByText("forecast")).toBeInTheDocument();
});

it("does not present missing values as measured and refuses malformed snapshots", () => {
  expect(diagnosticsEvent({ ...event, provider_ms: -1 })).toBeNull();
  expect(diagnosticsEvent({ ...event, servers: [{ name: "x", tools: [null] }] })).toBeNull();
  render(<ChatDiagnostics turns={[]} onClose={vi.fn()} />);
  expect(screen.getByText(/Send a message to measure a turn/)).toBeInTheDocument();
});

it("allows a person to inspect an earlier turn without replacing the latest measurement", () => {
  const first = diagnosticsEvent(event);
  const second = diagnosticsEvent({ ...event, preparation_ms: 77 });
  if (!first || !second) throw new Error("Expected diagnostics");
  render(
    <ChatDiagnostics
      turns={[
        { id: "one", snapshot: first },
        { id: "two", snapshot: second },
      ]}
      onClose={vi.fn()}
    />,
  );
  expect(screen.getByText("Preparation: 77 ms")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: "Turn" }), { target: { value: "one" } });
  expect(screen.getByText("Preparation: 23 ms")).toBeInTheDocument();
});

it("shows configured servers with unknown tool counts and sampled status", () => {
  const snapshot = diagnosticsEvent({
    ...event,
    servers: [
      { name: "my_server", internal: false, offered: false, tool_count: null, tools: [], scope: "user", running: true },
    ],
  });
  if (!snapshot) throw new Error("Expected diagnostics");
  render(<ChatDiagnostics turns={[{ id: "one", snapshot }]} onClose={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: /my_server/ }));
  expect(screen.getByText("Not offered in this request · tool count unknown")).toBeInTheDocument();
  expect(screen.getByText(/Running when sampled/)).toBeInTheDocument();
});

it("distinguishes tool categories and passive cached counts from offered tools", () => {
  const snapshot = diagnosticsEvent({
    ...event,
    internal_tool_ms: 12,
    mcp_discovery_ms: 100,
    mcp_tool_ms: 25,
    servers: [{ name: "weather", internal: false, offered: false, tool_count: null, tools: [], cached_tool_count: 7 }],
  });
  if (!snapshot) throw new Error("Expected diagnostics");
  render(<ChatDiagnostics turns={[{ id: "one", snapshot }]} onClose={vi.fn()} />);
  expect(screen.getByText("Internal tool calls: 12 ms")).toBeInTheDocument();
  expect(screen.getByText("MCP discovery: 100 ms")).toBeInTheDocument();
  expect(screen.getByText("MCP tool calls: 25 ms")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /weather/ }));
  expect(screen.getByText("Not offered in this request · tool count unknown")).toBeInTheDocument();
  expect(screen.getByText("Cached descriptors when sampled: 7")).toBeInTheDocument();
  expect(diagnosticsEvent({ ...event, mcp_tool_ms: -1 })).toBeNull();
  expect(diagnosticsEvent({ ...event, internal_tool_ms: Infinity })).toBeNull();
});
