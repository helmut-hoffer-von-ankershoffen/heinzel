// The server's automatic-update plan (ADR 0116), as every window and both switches read it.

import { useQuery } from "@tanstack/react-query";
import { useConfig } from "@/api/queries";
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
  /** The line both switches show, disabled: development, or update checks off. */
  held?: string | null;
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

/** The one line both switches show, disabled, when the setting can't turn updates on here: this hz runs for
 * development, or update checks are off. Null when the switch works. It is asked again when update checks change. */
export function useAutoUpdateHeld(): string | null {
  const config = useConfig();
  const checks = config.data?.settings.find((entry) => entry.section === "hz" && entry.key === "update_check");
  const query = useQuery({
    queryKey: [...KEY, "held", checks?.value ?? checks?.default ?? null],
    queryFn: ({ signal }) => call("GET", "", signal),
    staleTime: Number.POSITIVE_INFINITY, // development facts don't change while the server runs; checks are in the key
    retry: false,
  });
  return query.data?.held ?? query.data?.development ?? null;
}
