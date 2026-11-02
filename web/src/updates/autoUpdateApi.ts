// The server's automatic-update plan (ADR 0116), as every window and both switches read it.

import { useQuery } from "@tanstack/react-query";
import { token } from "@/api/token";

export type AutoUpdateStatus = {
  enabled: boolean;
  reason: string | null;
  phase: "off" | "idle" | "waiting" | "countdown" | "applying";
  version: string;
  core: string | null;
  plugins: Record<string, string>;
  seconds_left: number | null;
  apply_at: string | null;
  next_window_at: string | null;
  postponements: number;
  message: string | null;
  /** Why this hz runs for development: it never updates itself, and both switches are disabled with this line. */
  development?: string | null;
};

export const KEY = ["web", "auto-update"] as const;

export async function call(method: "GET" | "POST", path: string, signal?: AbortSignal): Promise<AutoUpdateStatus> {
  const response = await fetch(
    new Request(new URL(`/api/auto-update${path}`, window.location.origin), {
      method,
      cache: "no-store",
      signal,
      headers: { Authorization: `Bearer ${token() ?? ""}` },
    }),
  );
  if (!response.ok) throw new Error(`Automatic updates answered ${response.status}.`);
  return (await response.json()) as AutoUpdateStatus;
}

/** Why automatic updates never run here, because this hz runs for development; null for an installed hz. */
export function useAutoUpdateDevelopment(): string | null {
  const query = useQuery({
    queryKey: [...KEY, "development"],
    queryFn: ({ signal }) => call("GET", "", signal),
    staleTime: Number.POSITIVE_INFINITY, // facts about how this server was started: they don't change while it runs
    retry: false,
  });
  return query.data?.development ?? null;
}
