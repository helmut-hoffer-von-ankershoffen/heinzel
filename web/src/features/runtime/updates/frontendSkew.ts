import { compareCodeUnits } from "@/lib/sort";
import { loadedEntrypoints, type UpdateTransport, updateTransport } from "./controller";

const ATTEMPT = "hz.frontend.skew-reload";
const ASSET = /^\/assets\/[A-Za-z0-9_.-]+\.(?:js|css)$/;

/** Return a verified mismatch; a reload is claimed only once the person's work no longer holds it. */
export async function frontendSkew(
  version: string,
  signal: AbortSignal,
  transport: UpdateTransport = updateTransport,
  loaded: () => string[] = loadedEntrypoints,
  storage: Storage = sessionStorage,
): Promise<string | null> {
  const current = loaded();
  if (!current.length || current.some((path) => !ASSET.test(path))) return null;
  const build = await transport.frontend(signal);
  if (build.version !== version) return null;
  if (
    JSON.stringify([...current].sort(compareCodeUnits)) ===
    JSON.stringify([...build.entrypoints].sort(compareCodeUnits))
  )
    return null;
  if (storage.getItem(ATTEMPT) === build.identity) return null;
  // The server's no-store HTML and each asset must match its descriptor before replacing an open renderer.
  await transport.assets(build, signal, false);
  signal.throwIfAborted();
  return build.identity;
}

/** A single attempt per build and tab; unavailable storage fails closed against a reload loop. */
export function claimFrontendReload(identity: string, storage?: Storage): boolean {
  try {
    const target = storage ?? window.sessionStorage;
    if (target.getItem(ATTEMPT) === identity) return false;
    target.setItem(ATTEMPT, identity);
    return target.getItem(ATTEMPT) === identity;
  } catch {
    return false;
  }
}
