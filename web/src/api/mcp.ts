// MCP for other agents (ADR 0162): the switch and the tokens, under `/api/_web/mcp`. These are not operations, so no MCP
// head offers them; only this signed-in app reaches them.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ApiError, problem } from "./client";
import { token } from "./token";

export type McpToken = {
  id: string;
  actor: string;
  scope: "read" | "write";
  created: string;
  expires: string;
  last_used: string | null;
};
export type McpState = { enabled: boolean; tokens: McpToken[] };
/** A new token: the secret is in this answer only, and never again. */
export type McpMade = McpToken & { token: string };

export const mcpKey = ["_web", "mcp"] as const;
const PATH = `${window.location.origin}/api/_web/mcp`;

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await globalThis.fetch(
    new Request(`${PATH}${path}`, {
      ...init,
      headers: { Authorization: `Bearer ${token() ?? ""}`, "Content-Type": "application/json" },
      signal: AbortSignal.timeout(15_000),
    }),
  );
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw problem(response.status, body);
  return body as T;
}

export const useMcp = () => useQuery({ queryKey: mcpKey, queryFn: () => call<McpState>(""), refetchInterval: 5000 });

export function useSwitchMcp() {
  const client = useQueryClient();
  return useMutation<McpState, ApiError, boolean>({
    mutationFn: (enabled) => call<McpState>("", { method: "PUT", body: JSON.stringify({ enabled }) }),
    onSuccess: (state) => client.setQueryData(mcpKey, state),
  });
}

export function useMakeMcpToken() {
  const client = useQueryClient();
  return useMutation<McpMade, ApiError, { actor: string; scope: "read" | "write" }>({
    mutationFn: (body) => call<McpMade>("/tokens", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => client.invalidateQueries({ queryKey: mcpKey }),
  });
}

export function useRevokeMcpToken() {
  const client = useQueryClient();
  return useMutation<unknown, ApiError, string>({
    mutationFn: (id) => call(`/tokens/${encodeURIComponent(id)}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: mcpKey }),
  });
}

/** What one agent changed, from `hz system writes list`: the line under a token. */
export type AgentActivity = { caller: string; ran: number; ran_today: number; refused: number; last: string | null };
export const writesKey = ["system", "writes", "list"] as const;

/** `hz system writes list`: a line per agent. Read with the tokens, so a token's changes show beside it. */
export const useAgentWrites = () =>
  useQuery({
    queryKey: writesKey,
    queryFn: async () => {
      const response = await globalThis.fetch(
        new Request(`${window.location.origin}/api/system/writes/list?limit=1`, {
          headers: { Authorization: `Bearer ${token() ?? ""}` },
          signal: AbortSignal.timeout(15_000),
        }),
      );
      const body: unknown = await response.json().catch(() => null);
      if (!response.ok) throw problem(response.status, body);
      return ((body as { callers?: AgentActivity[] } | null)?.callers ?? []) as AgentActivity[];
    },
    refetchInterval: 30_000,
    // An older hz has no record of agents' writes: the tokens show without that line.
    retry: false,
  });

/** The activity of the token with this key id; undefined while unknown or when it never asked for a write. */
export const activityOf = (lines: AgentActivity[] | undefined, id: string): AgentActivity | undefined =>
  lines?.find((line) => line.caller === `token:${id}`);

/** The line that connects Claude Code; the token is the one just made, or a placeholder to fill in. */
export const claudeAddLine = (origin: string, secret = "<token>") =>
  `claude mcp add --transport http hz ${origin}/mcp --header "Authorization: Bearer ${secret}"`;
