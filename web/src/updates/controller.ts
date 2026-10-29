import { api, type SchemaUpdateResult, unwrap } from "@/api/client";
import { token } from "@/api/token";
import {
  clearOwnedShellCaches,
  type Lifecycle,
  type LifecycleTransport,
  lifecycleTransport,
  restartAndApply,
} from "@/lifecycle/controller";
import { preserveUpdateWork } from "./work";

export type UpdateStatus = {
  current: string;
  latest: string | null;
  installed_at: string | null;
  available: boolean;
  supported: boolean;
  reason: string | null;
};
export type Frontend = { version: string; identity: string; entrypoints: string[]; files: Record<string, string> };
type Receipt = {
  stage: "uncertain" | "installed" | "reloading";
  result?: SchemaUpdateResult;
  running?: Lifecycle;
  frontend?: Frontend;
};
const KEY = "hz.update.receipt";
const HASH = /^[a-f0-9]{64}$/;
const ASSET = /^\/assets\/[A-Za-z0-9_.-]+$/;

export interface UpdateTransport {
  lifecycle: LifecycleTransport;
  install(signal: AbortSignal): Promise<SchemaUpdateResult>;
  frontend(signal: AbortSignal): Promise<Frontend>;
  assets(build: Frontend, signal: AbortSignal, loaded: boolean): Promise<void>;
  clean(): Promise<void>;
  reload(): void;
}

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(
    new Request(new URL(`/api${path}`, window.location.origin), {
      cache: "no-store",
      signal,
      headers: { Authorization: `Bearer ${token() ?? ""}` },
    }),
  );
  if (!response.ok) throw new Error(`Update verification was refused (${response.status}).`);
  return response.json() as Promise<T>;
}

export const readUpdateStatus = (signal: AbortSignal) => read<UpdateStatus>("/update-status", signal);

function validFrontend(value: Frontend): Frontend {
  if (
    !value ||
    !HASH.test(value.identity) ||
    typeof value.version !== "string" ||
    !value.entrypoints?.length ||
    !value.files ||
    Object.keys(value.files).length > 128 ||
    value.entrypoints.some((path) => !ASSET.test(path) || !value.files[path]) ||
    Object.entries(value.files).some(([path, hash]) => !ASSET.test(path) || !HASH.test(hash))
  )
    throw new Error("The installed frontend descriptor is invalid.");
  return value;
}

export function loadedEntrypoints(document_: Document = document): string[] {
  return [...document_.querySelectorAll<HTMLScriptElement | HTMLLinkElement>('script[src],link[rel="stylesheet"]')]
    .map(
      (element) =>
        new URL(element.getAttribute(element.tagName === "SCRIPT" ? "src" : "href") ?? "", window.location.origin),
    )
    .map((url) => (url.origin === window.location.origin ? url.pathname : ""))
    .sort();
}

async function verifyAssets(build: Frontend, signal: AbortSignal, loaded: boolean): Promise<void> {
  validFrontend(build);
  if (loaded && JSON.stringify(loadedEntrypoints()) !== JSON.stringify([...build.entrypoints].sort()))
    throw new Error("The page still has different frontend assets. Reload again to finish updating.");
  const html = await fetch(new Request(new URL("/", window.location.origin), { signal, cache: "no-store" }));
  if (
    !html.ok ||
    JSON.stringify(loadedEntrypoints(new DOMParser().parseFromString(await html.text(), "text/html"))) !==
      JSON.stringify([...build.entrypoints].sort())
  )
    throw new Error("The current HTML does not match the verified frontend build.");
  for (const [path, expected] of Object.entries(build.files)) {
    const response = await fetch(new Request(new URL(path, window.location.origin), { signal, cache: "reload" }));
    if (!response.ok) throw new Error("An installed frontend asset is unavailable.");
    const bytes = await response.arrayBuffer();
    if (bytes.byteLength > 8 * 1024 * 1024) throw new Error("An installed frontend asset exceeded its limit.");
    const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
    if (hash !== expected) throw new Error("An installed frontend asset did not match its verified bytes.");
  }
}

export const updateTransport: UpdateTransport = {
  lifecycle: lifecycleTransport,
  install: (signal) => unwrap(api.POST("/system/update", { signal })),
  frontend: async (signal) => validFrontend(await read<Frontend>("/frontend", signal)),
  assets: verifyAssets,
  clean: async () => {
    if (window.caches) await clearOwnedShellCaches();
    const worker = await navigator.serviceWorker?.getRegistration("/");
    // Another application's root-scope worker is never updated or unregistered by hz.
    if (worker?.active && worker.active.scriptURL === new URL("/sw.js", window.location.origin).href)
      await worker.update();
  },
  reload: () => window.location.reload(),
};

function save(receipt: Receipt): void {
  const encoded = JSON.stringify(receipt);
  sessionStorage.setItem(KEY, encoded);
  if (sessionStorage.getItem(KEY) !== encoded) throw new Error("Update recovery could not be saved in this tab.");
}

export function pendingUpdate(): Receipt | null {
  const text = sessionStorage.getItem(KEY);
  if (!text) return null;
  try {
    const value = JSON.parse(text) as Receipt;
    if (!["uncertain", "installed", "reloading"].includes(value.stage)) throw new Error();
    return value;
  } catch {
    throw new Error("Saved update recovery data is invalid; inspect the installation before another update.");
  }
}

async function bounded<T>(run: (signal: AbortSignal) => Promise<T>, owner: AbortSignal, timeout: number): Promise<T> {
  owner.throwIfAborted();
  const controller = new AbortController();
  const signal = AbortSignal.any([owner, controller.signal]);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stop = () => {};
  try {
    return await Promise.race([
      run(signal),
      new Promise<never>((_resolve, reject) => {
        stop = () => reject(signal.reason);
        signal.addEventListener("abort", stop, { once: true });
        timer = setTimeout(
          () => controller.abort(new Error("The update step did not finish; inspect its outcome before retrying.")),
          timeout,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", stop);
    controller.abort();
  }
}

/** Only the explicit update gesture calls this; any uncertain attempt remains recorded and is never replayed. */
export async function startUpdate(
  signal: AbortSignal,
  onPhase: (phase: string) => void,
  transport = updateTransport,
): Promise<string> {
  if (pendingUpdate()) throw new Error("An update needs verification; installation will not be repeated.");
  const before = await bounded((signal) => transport.lifecycle.read(signal), signal, 4_000);
  if (!before.restartable) throw new Error("This server cannot restart; update it where it was installed.");
  preserveUpdateWork();
  save({ stage: "uncertain" });
  onPhase("Updating core and plugins…");
  const result = await bounded((signal) => transport.install(signal), signal, 900_000);
  if (!result || typeof result.after !== "string" || !result.after.trim() || !Array.isArray(result.plugins))
    throw new Error("The updater returned no verifiable result; inspect the installation.");
  if (!result.updated) {
    sessionStorage.removeItem(KEY);
    return result.message;
  }
  save({ stage: "installed", result });
  preserveUpdateWork();
  onPhase("Restarting and verifying hz…");
  const running = await restartAndApply(
    { confirmed: true, version: result.after },
    { signal, transport: transport.lifecycle },
  );
  const frontend = await bounded((signal) => transport.frontend(signal), signal, 4_000);
  if (frontend.version !== running.version) throw new Error("The frontend belongs to a different backend version.");
  onPhase("Checking frontend assets…");
  await bounded((signal) => transport.assets(frontend, signal, false), signal, 30_000);
  await bounded(() => transport.clean(), signal, 15_000);
  preserveUpdateWork();
  save({ stage: "reloading", result, running, frontend });
  transport.reload();
  return "Reloading to verify the updated app…";
}

/** After reload this only checks; it cannot repeat installation or restart. */
export async function finishUpdate(signal: AbortSignal, transport = updateTransport): Promise<string | null> {
  const receipt = pendingUpdate();
  if (!receipt) return null;
  if (receipt.stage !== "reloading" || !receipt.running || !receipt.frontend || !receipt.result)
    throw new Error("The update outcome needs inspection. Check hz system info; installation was not repeated.");
  const running = await bounded((signal) => transport.lifecycle.read(signal), signal, 4_000);
  if (running.instance !== receipt.running.instance || running.version !== receipt.result.after)
    throw new Error("The reloaded app is not connected to the verified replacement version.");
  const frontend = await bounded((signal) => transport.frontend(signal), signal, 4_000);
  if (frontend.version !== running.version || frontend.identity !== receipt.frontend.identity)
    throw new Error("The frontend build changed before reload verification completed.");
  await bounded((signal) => transport.assets(frontend, signal, true), signal, 30_000);
  sessionStorage.removeItem(KEY);
  return receipt.result.message;
}
