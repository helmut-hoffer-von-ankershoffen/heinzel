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
  /** Installed plugins from git, a folder or a URL: automatic updates never touch them; a person updates them. */
  by_hand?: string[];
};

export const KEY = ["web", "auto-update"] as const;

export async function call(method: "GET" | "POST", path: string, signal?: AbortSignal): Promise<AutoUpdateStatus> {
  const read = async (signal?: AbortSignal): Promise<AutoUpdateStatus> => {
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
  };
  // Postpone and dismiss must keep their original request and response, even on a slow connection.
  if (method === "POST") return read(signal);
  // A read stalled across restart can hold later query polls behind it. Try one additional read without cancelling
  // a slow valid response; only a successful answer makes the other request redundant. Never grow past two reads.
  const redundant = new AbortController();
  const owned = signal ? AbortSignal.any([signal, redundant.signal]) : redundant.signal;
  let started = false;
  let refuseRecovery = (_error: unknown) => {};
  let timer: ReturnType<typeof setTimeout> | undefined;
  const recovery = new Promise<AutoUpdateStatus>((resolve, reject) => {
    refuseRecovery = reject;
    timer = setTimeout(() => {
      started = true;
      void read(owned).then(resolve, reject);
    }, 4_000);
  });
  const original = read(owned).catch((error: unknown) => {
    if (!started) refuseRecovery(error); // a prompt failure keeps the existing polling/error behavior
    throw error;
  });
  try {
    return await Promise.any([original, recovery]);
  } catch (error) {
    signal?.throwIfAborted();
    throw error instanceof AggregateError ? error.errors[0] : error;
  } finally {
    clearTimeout(timer);
    redundant.abort();
  }
}

/** The live server plan shared by the update pane and automatic countdown. Reads only cached facts. */
export function useAutoUpdateStatus(poll = false) {
  return useQuery({
    queryKey: KEY,
    queryFn: ({ signal }) => call("GET", "", signal),
    refetchInterval: poll ? 2_000 : false,
    refetchIntervalInBackground: true,
    retry: false,
  });
}

/** The one line both switches show, disabled, when the setting can't turn updates on here: this hz runs for
 * development, or update checks are off. Null when the switch works. It is asked again when update checks change. */
export function useAutoUpdateHeld(): string | null {
  const status = useAutoUpdateFacts();
  return status?.held ?? status?.development ?? null;
}

/** What the server says about this installation, asked once and again when update checks change. */
export function useAutoUpdateFacts(): AutoUpdateStatus | undefined {
  const config = useConfig();
  const checks = config.data?.settings.find((entry) => entry.section === "hz" && entry.key === "update_check");
  const query = useQuery({
    queryKey: [...KEY, "held", checks?.value ?? checks?.default ?? null],
    queryFn: ({ signal }) => call("GET", "", signal),
    staleTime: Number.POSITIVE_INFINITY, // development facts don't change while the server runs; checks are in the key
    retry: false,
  });
  return query.data;
}
