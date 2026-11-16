import { useState } from "react";
import { Button } from "@/components/ui/button";

type Tool = { name: string; description: string };
type Server = {
  name: string;
  internal: boolean;
  offered: boolean;
  tools: Tool[];
  tool_count: number | null;
  scope?: string | null;
  running?: boolean | null;
  cached_tool_count?: number | null;
};
export type Diagnostics = {
  preparation_ms: number;
  discovery_ms: number;
  provider_ms: number;
  internal_tool_ms?: number;
  mcp_discovery_ms?: number;
  mcp_tool_ms?: number;
  requests: number;
  context: {
    messages?: number;
    input_tokens?: number | null;
    window_tokens?: number | null;
    instruction_chars?: number | null;
    user_parts?: number;
    system_parts?: number;
    tool_results?: number;
    responses?: number;
  };
  servers: Server[];
};

/** Treat the stream as data, not markup, and ignore malformed or oversized snapshots. */
export function diagnosticsEvent(value: Record<string, unknown>): Diagnostics | null {
  const number = (v: unknown) => typeof v === "number" && Number.isFinite(v) && v >= 0;
  if (
    !number(value.preparation_ms) ||
    !number(value.discovery_ms) ||
    !number(value.provider_ms) ||
    !number(value.requests) ||
    !value.context ||
    typeof value.context !== "object" ||
    !Array.isArray(value.servers) ||
    value.servers.length > 512
  )
    return null;
  for (const server of value.servers) {
    if (
      !server ||
      typeof server !== "object" ||
      typeof server.name !== "string" ||
      server.name.length > 128 ||
      typeof server.internal !== "boolean" ||
      typeof server.offered !== "boolean" ||
      (server.scope != null && !["user", "project", "plugin"].includes(server.scope)) ||
      (server.running != null && typeof server.running !== "boolean") ||
      (server.cached_tool_count != null && !number(server.cached_tool_count)) ||
      !Array.isArray(server.tools) ||
      (server.offered && (!number(server.tool_count) || server.tool_count < server.tools.length)) ||
      (!server.offered && (server.tool_count !== null || server.tools.length !== 0)) ||
      server.tools.length > 2048 ||
      server.tools.some(
        (tool: unknown) =>
          !tool ||
          typeof tool !== "object" ||
          typeof (tool as Tool).name !== "string" ||
          (tool as Tool).name.length > 128 ||
          typeof (tool as Tool).description !== "string" ||
          (tool as Tool).description.length > 512,
      )
    )
      return null;
  }
  for (const timing of [value.internal_tool_ms, value.mcp_discovery_ms, value.mcp_tool_ms])
    if (timing != null && !number(timing)) return null;
  const context = value.context as Diagnostics["context"];
  for (const n of [
    context.messages,
    context.input_tokens,
    context.window_tokens,
    context.instruction_chars,
    context.user_parts,
    context.system_parts,
    context.tool_results,
    context.responses,
  ])
    if (n != null && !number(n)) return null;
  return value as Diagnostics;
}

const duration = (ms: number) => (ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`);

export function ChatDiagnostics({
  turns,
  onClose,
}: {
  turns: { id: string; snapshot: Diagnostics }[];
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [turnId, setTurnId] = useState<string | null>(null);
  const snapshot = turns.find((turn) => turn.id === turnId)?.snapshot ?? turns.at(-1)?.snapshot ?? null;
  const server = snapshot?.servers.find((item) => `${item.internal}:${item.name}` === selected);
  return (
    <aside
      aria-label="Chat diagnostics"
      className="absolute inset-y-12 right-0 z-20 flex w-full max-w-80 flex-col border-l border-border bg-background shadow-xl"
    >
      <div className="flex items-center justify-between border-b border-border p-3">
        <h3 className="font-medium">{server ? server.name : "Chat diagnostics"}</h3>
        <Button type="button" variant="ghost" size="sm" onClick={server ? () => setSelected(null) : onClose}>
          {server ? "Back" : "Close"}
        </Button>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3 text-sm">
        {turns.length > 0 && (
          <label className="flex items-center gap-2">
            Turn
            <select
              className="min-h-11 rounded border border-border bg-input px-2"
              value={turnId ?? turns.at(-1)?.id}
              onChange={(event) => {
                setTurnId(event.target.value);
                setSelected(null);
              }}
            >
              {turns.map((turn, index) => (
                <option key={turn.id} value={turn.id}>
                  Turn {index + 1}
                </option>
              ))}
            </select>
          </label>
        )}
        {!snapshot ? (
          <p>Send a message to measure a turn. Earlier turns are not available.</p>
        ) : server ? (
          <section>
            <p>
              {server.offered
                ? `Offered · ${server.tool_count} tools`
                : snapshot.context.messages == null
                  ? "Not measured yet"
                  : "Not offered in this request · tool count unknown"}
            </p>
            <p className="text-muted-foreground">
              {server.internal ? "Heinzel operations" : `${server.scope ?? "External"} MCP server`}
              {!server.internal &&
                ` · ${server.running == null ? "Connection status unavailable" : server.running ? "Running when sampled" : "Not running when sampled"}`}
            </p>
            <ul className="mt-3 space-y-3">
              {server.tools.map((tool) => (
                <li key={tool.name}>
                  <strong className="break-all">{tool.name}</strong>
                  {tool.description && <p className="break-words text-muted-foreground">{tool.description}</p>}
                </li>
              ))}
            </ul>
            {server.cached_tool_count != null && <p>Cached descriptors when sampled: {server.cached_tool_count}</p>}
            {server.tool_count != null && server.tool_count > server.tools.length && (
              <p>Showing the first {server.tools.length} tools.</p>
            )}
          </section>
        ) : (
          <>
            <section className="space-y-1">
              <h4 className="font-medium">Timings</h4>
              <p>Preparation: {duration(snapshot.preparation_ms)}</p>
              <p>Tool preparation: {duration(snapshot.discovery_ms)}</p>
              <p>
                Internal tool calls:{" "}
                {snapshot.internal_tool_ms == null ? "Not measured" : duration(snapshot.internal_tool_ms)}
              </p>
              <p>
                MCP discovery:{" "}
                {snapshot.mcp_discovery_ms == null ? "Not measured" : duration(snapshot.mcp_discovery_ms)}
              </p>
              <p>MCP tool calls: {snapshot.mcp_tool_ms == null ? "Not measured" : duration(snapshot.mcp_tool_ms)}</p>
              <p className="text-xs text-muted-foreground">
                Tool totals include failures and may overlap when calls run together.
              </p>
              <p>
                Provider requests: {snapshot.requests ? duration(snapshot.provider_ms) : "Not measured yet"} (
                {snapshot.requests})
              </p>
            </section>
            <section className="space-y-1">
              <h4 className="font-medium">Context</h4>
              <p>Messages in latest request: {snapshot.context.messages ?? "Not measured yet"}</p>
              <p>
                Instructions:{" "}
                {snapshot.context.instruction_chars == null
                  ? "Not measured"
                  : `${snapshot.context.instruction_chars} characters`}
              </p>
              {snapshot.context.messages != null && (
                <p>
                  Parts: {snapshot.context.user_parts ?? 0} user, {snapshot.context.system_parts ?? 0} system,{" "}
                  {snapshot.context.tool_results ?? 0} tool results, {snapshot.context.responses ?? 0} model responses
                </p>
              )}
              <p>
                Input tokens: {snapshot.context.input_tokens ?? "Not reported"}
                {snapshot.context.window_tokens ? ` / ${snapshot.context.window_tokens}` : ""}
              </p>
              <p className="text-muted-foreground">Context contents and estimated token breakdown are not available.</p>
            </section>
            <section className="space-y-1">
              <h4 className="font-medium">
                Offered servers · {snapshot.servers.filter((item) => item.offered).length}
              </h4>
              {snapshot.context.messages == null && <p>Not measured yet.</p>}
              {snapshot.servers.map((item) => (
                <Button
                  key={`${item.internal}:${item.name}`}
                  type="button"
                  variant="ghost"
                  className="w-full justify-between"
                  onClick={() => setSelected(`${item.internal}:${item.name}`)}
                >
                  <span className="truncate">
                    {!item.internal && item.name === "Internal" ? "Internal (MCP)" : item.name}
                  </span>
                  <span>{item.tool_count == null ? "Not offered" : `${item.tool_count} tools`}</span>
                </Button>
              ))}
            </section>
          </>
        )}
      </div>
    </aside>
  );
}
