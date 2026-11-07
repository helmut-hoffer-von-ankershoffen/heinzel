import type { SchemaUpdateResult } from "@/api/client";
import { stream } from "@/api/stream";
import { token } from "@/api/token";
import { mergePhase, type Phase, type PhaseState, parsePhase } from "@/lib/operationRun";
import { compareCodeUnits } from "@/lib/sort";
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
  /** Install hz and its plugins, telling each step the server reports as it happens. */
  install(signal: AbortSignal, onPhase?: (phase: Phase) => void): Promise<SchemaUpdateResult>;
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
    .sort(compareCodeUnits);
}

async function verifyAssets(build: Frontend, signal: AbortSignal, loaded: boolean): Promise<void> {
  validFrontend(build);
  if (loaded && JSON.stringify(loadedEntrypoints()) !== JSON.stringify([...build.entrypoints].sort(compareCodeUnits)))
    throw new Error("The page still has different frontend assets. Reload again to finish updating.");
  const html = await fetch(new Request(new URL("/", window.location.origin), { signal, cache: "no-store" }));
  if (
    !html.ok ||
    JSON.stringify(loadedEntrypoints(new DOMParser().parseFromString(await html.text(), "text/html"))) !==
      JSON.stringify([...build.entrypoints].sort(compareCodeUnits))
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
  install: (signal, onPhase) =>
    stream<SchemaUpdateResult>(
      "/system/update",
      {},
      ({ event, data }) => {
        const phase = event === "phase" ? parsePhase(data) : null;
        if (phase) onPhase?.(phase);
      },
      signal,
    ),
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

/**
 * The update's steps as one list: the server's (asking, installing, each plugin, the background services) as it
 * reports them, then the app's own (restart, files, reload), each told when it starts and ends.
 */
class UpdateSteps {
  private server: Phase[] = [];
  private local: Phase[] = [
    { id: "restart", label: "Restarting hz and checking its version", state: "pending" },
    { id: "frontend", label: "Checking the app's files", state: "pending" },
    { id: "reload", label: "Reloading the app", state: "pending" },
  ];
  private running: string | null = null;
  constructor(private readonly report: (phases: Phase[]) => void) {}
  private tell() {
    this.report([...this.server, ...this.local]);
  }
  /** A step the server reported; a later report of the same step replaces it. */
  readonly heard = (phase: Phase) => {
    this.server = mergePhase(this.server, phase);
    this.tell();
  };
  mark(id: string, state: PhaseState) {
    this.local = this.local.map((entry) => (entry.id === id ? { ...entry, state } : entry));
    this.running = state === "running" ? id : this.running === id ? null : this.running;
    this.tell();
  }
  /** The app's step that was running failed with the error that stopped the update. */
  failed(error: unknown) {
    if (!this.running) return;
    const detail = error instanceof Error ? error.message : String(error);
    this.local = this.local.map((entry) => (entry.id === this.running ? { ...entry, state: "failed", detail } : entry));
    this.running = null;
    this.tell();
  }
}

/** Only the explicit update gesture calls this; any uncertain attempt remains recorded and is never replayed. */
export async function startUpdate(
  signal: AbortSignal,
  onPhase: (phase: string) => void,
  transport = updateTransport,
  onSteps: (phases: Phase[]) => void = () => {},
): Promise<string> {
  if (pendingUpdate()) throw new Error("An update needs verification; installation will not be repeated.");
  const before = await bounded((signal) => transport.lifecycle.read(signal), signal, 4_000);
  if (!before.restartable) throw new Error("This server cannot restart; update it where it was installed.");
  preserveUpdateWork();
  save({ stage: "uncertain" });
  onPhase("Updating core and plugins…");
  const steps = new UpdateSteps(onSteps);
  const result = await bounded((signal) => transport.install(signal, steps.heard), signal, 900_000);
  if (!result || typeof result.after !== "string" || !result.after.trim() || !Array.isArray(result.plugins))
    throw new Error("The updater returned no verifiable result; inspect the installation.");
  if (!result.updated) {
    sessionStorage.removeItem(KEY);
    return result.message;
  }
  save({ stage: "installed", result });
  try {
    preserveUpdateWork();
    onPhase("Restarting and verifying hz…");
    steps.mark("restart", "running");
    const running = await restartAndApply(
      { confirmed: true, version: result.after },
      { signal, transport: transport.lifecycle },
    );
    steps.mark("restart", "done");
    save({ stage: "installed", result, running });
    return await reloadVerifiedUpdate(result, running, signal, transport, onPhase, steps);
  } catch (error) {
    steps.failed(error);
    throw error;
  }
}

async function reloadVerifiedUpdate(
  result: SchemaUpdateResult,
  running: Lifecycle,
  signal: AbortSignal,
  transport: UpdateTransport,
  onPhase: (phase: string) => void = () => {},
  steps?: UpdateSteps,
): Promise<string> {
  steps?.mark("frontend", "running");
  const frontend = await bounded((signal) => transport.frontend(signal), signal, 4_000);
  if (frontend.version !== running.version) throw new Error("The frontend belongs to a different backend version.");
  onPhase("Checking frontend assets…");
  await bounded((signal) => transport.assets(frontend, signal, false), signal, 30_000);
  await bounded(() => transport.clean(), signal, 15_000);
  steps?.mark("frontend", "done");
  preserveUpdateWork();
  save({ stage: "reloading", result, running, frontend });
  steps?.mark("reload", "running");
  transport.reload();
  return "Reloading to verify the updated app…";
}

export function canVerifyUpdate(receipt: Receipt): receipt is Receipt & Required<Pick<Receipt, "result" | "running">> {
  const { stage, result, running, frontend } = receipt;
  if (
    result?.updated !== true ||
    typeof result.after !== "string" ||
    !result.after.trim() ||
    typeof result.message !== "string" ||
    typeof running?.instance !== "string" ||
    !running.instance.trim() ||
    running.version !== result.after
  )
    return false;
  if (stage === "installed") return true;
  if (stage !== "reloading" || !frontend || frontend.version !== running.version) return false;
  try {
    validFrontend(frontend);
    return true;
  } catch {
    return false;
  }
}

/** Observe the recorded replacement and frontend without repeating installation or restart. */
export async function finishUpdate(signal: AbortSignal, transport = updateTransport): Promise<string | null> {
  const receipt = pendingUpdate();
  if (!receipt) return null;
  if (!canVerifyUpdate(receipt))
    throw new Error("Update verification needs more evidence. Run hz system info to inspect the installation.");
  const { result, running: expected } = receipt;
  const running = await bounded((signal) => transport.lifecycle.read(signal), signal, 4_000);
  if (running.instance !== expected.instance || running.version !== result.after)
    throw new Error("The reloaded app is not connected to the verified replacement version.");
  if (receipt.stage === "installed") return reloadVerifiedUpdate(result, running, signal, transport);
  const frontend = await bounded((signal) => transport.frontend(signal), signal, 4_000);
  if (frontend.version !== running.version || frontend.identity !== receipt.frontend?.identity)
    throw new Error("The frontend build changed before reload verification completed.");
  await bounded((signal) => transport.assets(frontend, signal, true), signal, 30_000);
  sessionStorage.removeItem(KEY);
  return receipt.result.message;
}
