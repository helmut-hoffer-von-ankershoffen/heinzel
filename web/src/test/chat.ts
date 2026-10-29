// Made-up chat sessions, and the stream the web head answers `POST /api/stream/...` with (ADR 0045).
import { vi } from "vitest";
import type { SchemaChatSession } from "@/api/client";
import { doctor, noTeam, shell } from "./fixtures";
import { mockApi } from "./render";

export const base = { ...shell, "/api/system/doctor": doctor, "/api/team/show": noTeam, "/api/system/daemon/jobs": [] };

export const at = "2026-10-24T09:00:00Z";
export const costs = { spent_usd: 0, session_max_usd: 2, message_max_usd: 0.25 };

export type Message = NonNullable<SchemaChatSession["messages"]>[number];
export const msg = (fields: Partial<Message> & Pick<Message, "role" | "text">): Message => ({
  at,
  status: "completed",
  detail: "",
  fix: "",
  runner: "",
  ...fields,
});

export const empty: SchemaChatSession = {
  id: "0123456789abcdef",
  title: "route /",
  created: at,
  updated: at,
  context: { kind: "route", ref: "/", detail: "" },
  messages: [],
  actions: [],
  costs,
};

export const proposal = {
  id: "a1",
  tool: "system_config_set",
  module: "system.config",
  operation: "set",
  command: "hz system config set --key org.role --value lead",
  arguments: { key: "org.role", value: "lead" },
  status: "pending" as const,
  mcp_authorized: false,
  result: "",
  proposed: at,
  decided: null,
};

export const answered: SchemaChatSession = {
  ...empty,
  messages: [
    msg({ role: "user", text: "Make me the lead" }),
    msg({
      role: "assistant",
      text: "I proposed setting your role to lead.",
      cost_usd: 0.0123,
      runner: "claude-code",
      actions: ["a1"],
    }),
  ],
  actions: [proposal],
  costs: { ...costs, spent_usd: 0.0123 },
};

/** The API, plus `POST /api/stream/...` answered with server-sent events, as the web head streams operations. */
export function mockWithStream(answers: Record<string, unknown>, events: string[]) {
  const { calls } = mockApi(answers);
  const json = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      if (!new URL(input.url).pathname.startsWith("/api/stream/")) return json(input);
      calls.push(input);
      return new Response(events.join(""), { headers: { "Content-Type": "text/event-stream" } });
    }),
  );
  return { calls };
}

export const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

/** Like `mockWithStream`, but the stream sends `first` at once and the rest only on `release()`: mid-answer. */
export function mockWithHeldStream(answers: Record<string, unknown>, first: string[], rest: string[]) {
  const { calls } = mockApi(answers);
  const json = globalThis.fetch;
  let release = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      if (!new URL(input.url).pathname.startsWith("/api/stream/")) return json(input);
      calls.push(input);
      const encoder = new TextEncoder();
      const body = new ReadableStream<Uint8Array>({
        async start(controller) {
          controller.enqueue(encoder.encode(first.join("")));
          await held;
          controller.enqueue(encoder.encode(rest.join("")));
          controller.close();
        },
      });
      return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
    }),
  );
  return { calls, release: () => release() };
}
