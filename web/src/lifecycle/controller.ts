import { ApiError, api, unwrap } from "@/api/client";
import { t } from "@/i18n";

export type Lifecycle = { instance: string; version: string; restartable: boolean };
export type ExpectedSetting = { section: string; key: string; value: unknown };
export type LoadedExpectation = { instance: string; version: string; settings: ExpectedSetting[] };
export type VerifiedLifecycle = { instance: string; version: string; matches: boolean; mismatched: string[] };
export type RestartAcknowledgement = { restarting: boolean; instance: string };

export interface LifecycleTransport {
  read(signal: AbortSignal): Promise<Lifecycle>;
  restart(signal: AbortSignal): Promise<RestartAcknowledgement>;
  verify(expected: LoadedExpectation, signal: AbortSignal): Promise<VerifiedLifecycle>;
}

/** One authenticated, uncached transport; the server compares safe target fields against its startup snapshot. */
export const lifecycleTransport: LifecycleTransport = {
  read: (signal) => unwrap(api.GET("/lifecycle", { signal, cache: "no-store" })),
  restart: (signal) => unwrap(api.POST("/restart", { signal, cache: "no-store" })),
  verify: (expected, signal) => unwrap(api.POST("/lifecycle/verify", { body: expected, signal, cache: "no-store" })),
};

/** Retain this observation after an uncertain POST or timeout; checking again must never replay the restart. */
export type RestartObservation = {
  retired: string[];
  version: string;
  settings: ExpectedSetting[];
};

export type ApplyPhase = "checking" | "preserving" | "restarting" | "reconnecting" | "verifying" | "applied";
export type ApplyFailure =
  | "confirmation"
  | "unsupported"
  | "refused"
  | "timeout"
  | "version"
  | "configuration"
  | "aborted";

export class RestartApplyError extends Error {
  constructor(
    readonly kind: ApplyFailure,
    message: string,
    readonly observation?: RestartObservation,
  ) {
    super(message);
  }
}

export type ApplyOptions = {
  transport?: LifecycleTransport;
  signal?: AbortSignal;
  timeout?: number;
  requestTimeout?: number;
  /** Brief readiness window before any mutation; each read keeps the short request timeout. */
  readinessTimeout?: number;
  /** Long-running confirmed work performed before restart, such as rebuilding the plugin environment. */
  preserveTimeout?: number;
  interval?: number;
  onPhase?: (phase: ApplyPhase) => void;
  onObservation?: (observation: RestartObservation) => void;
};

function abortReason(signal: AbortSignal): Error {
  return signal.reason instanceof Error ? signal.reason : new DOMException(t("lifecycle.cancelled"), "AbortError");
}

/** Bound even a transport that ignores cancellation, while cancelling actual fetches and removing every listener. */
async function bounded<T>(run: (signal: AbortSignal) => Promise<T>, signal: AbortSignal, timeout: number): Promise<T> {
  signal.throwIfAborted();
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancel = () => {};
  const interrupted = new Promise<never>((_resolve, reject) => {
    cancel = () => {
      controller.abort(signal.reason);
      reject(abortReason(signal));
    };
    signal.addEventListener("abort", cancel, { once: true });
    timer = setTimeout(() => {
      const error = new DOMException(t("lifecycle.requestTimeout"), "TimeoutError");
      controller.abort(error);
      reject(error);
    }, timeout);
  });
  try {
    return await Promise.race([run(controller.signal), interrupted]);
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
    controller.abort();
  }
}

function transient(error: unknown): boolean {
  return (
    error instanceof TypeError ||
    (error instanceof DOMException && (error.name === "TimeoutError" || error.name === "AbortError")) ||
    (error instanceof ApiError && error.status >= 500)
  );
}

function checkedLifecycle(value: Lifecycle): Lifecycle {
  if (
    !value ||
    typeof value.instance !== "string" ||
    !value.instance.trim() ||
    typeof value.version !== "string" ||
    !value.version.trim() ||
    typeof value.restartable !== "boolean"
  ) {
    throw new Error(t("lifecycle.invalidResponse"));
  }
  return value;
}

async function session<T>(options: ApplyOptions, run: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const controller = new AbortController();
  const cancel = () => controller.abort(options.signal?.reason);
  const timer = setTimeout(
    () => controller.abort(new DOMException(t("lifecycle.deadlineTimeout"), "TimeoutError")),
    options.timeout ?? 60_000,
  );
  options.signal?.addEventListener("abort", cancel, { once: true });
  if (options.signal?.aborted) cancel();
  try {
    return await run(controller.signal);
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", cancel);
  }
}

/** Retry a transient preflight failure because no confirmed change has been sent yet. */
async function readyBeforeChange(options: ApplyOptions, owner: AbortSignal): Promise<Lifecycle> {
  const transport = options.transport ?? lifecycleTransport;
  try {
    return await session({ ...options, signal: owner, timeout: options.readinessTimeout ?? 15_000 }, async (signal) => {
      for (;;) {
        try {
          return checkedLifecycle(
            await bounded((signal) => transport.read(signal), signal, options.requestTimeout ?? 4_000),
          );
        } catch (error) {
          if (!transient(error)) throw error;
        }
        await bounded(
          (signal) =>
            new Promise<void>((resolve) => {
              const timer = setTimeout(resolve, options.interval ?? 500);
              signal.addEventListener("abort", () => clearTimeout(timer), { once: true });
            }),
          signal,
          (options.interval ?? 500) + 1_000,
        );
      }
    });
  } catch (error) {
    if (options.signal?.aborted) throw new RestartApplyError("aborted", t("lifecycle.beforeChangeCancelled"));
    if (owner.aborted || (error instanceof DOMException && error.name === "TimeoutError")) {
      throw new RestartApplyError("timeout", t("lifecycle.beforeChangeTimeout"));
    }
    throw error;
  }
}

async function observe(
  observation: RestartObservation,
  options: ApplyOptions,
  signal: AbortSignal,
): Promise<Lifecycle> {
  return session({ ...options, signal, timeout: options.timeout ?? 60_000 }, (observeSignal) =>
    observeUntil(observation, options, observeSignal),
  );
}

async function observeUntil(
  observation: RestartObservation,
  options: ApplyOptions,
  signal: AbortSignal,
): Promise<Lifecycle> {
  const transport = options.transport ?? lifecycleTransport;
  let reason: "timeout" | "version" | "configuration" = "timeout";
  let message = t("lifecycle.timeout");
  const request = <T>(run: (signal: AbortSignal) => Promise<T>) =>
    bounded(run, signal, options.requestTimeout ?? 4_000);
  try {
    for (;;) {
      options.onPhase?.("reconnecting");
      try {
        const running = checkedLifecycle(await request((signal) => transport.read(signal)));
        if (!observation.retired.includes(running.instance)) {
          options.onPhase?.("verifying");
          if (running.version !== observation.version) {
            reason = "version";
            message = t("lifecycle.wrongVersion", { running: running.version, expected: observation.version });
          } else {
            const verified = await request((signal) =>
              transport.verify(
                { instance: running.instance, version: observation.version, settings: observation.settings },
                signal,
              ),
            );
            if (
              verified.instance === running.instance &&
              verified.version === observation.version &&
              verified.matches === true &&
              Array.isArray(verified.mismatched) &&
              verified.mismatched.length === 0
            ) {
              options.onPhase?.("applied");
              return running;
            }
            reason = "configuration";
            message = t("lifecycle.wrongSettings");
          }
        }
      } catch (error) {
        if (!transient(error)) throw error;
      }
      await bounded(
        (signal) =>
          new Promise<void>((resolve) => {
            const timer = setTimeout(resolve, options.interval ?? 500);
            signal.addEventListener("abort", () => clearTimeout(timer), { once: true });
          }),
        signal,
        (options.interval ?? 500) + 1_000,
      );
    }
  } catch (error) {
    if (signal.aborted) {
      if (options.signal?.aborted) throw new RestartApplyError("aborted", t("lifecycle.stopped"), observation);
      throw new RestartApplyError(reason, message, observation);
    }
    throw new RestartApplyError(
      "refused",
      error instanceof Error ? error.message : t("lifecycle.verifyRefused"),
      observation,
    );
  }
}

/** Observe a previous attempt without sending another POST, including after a dropped acknowledgement. */
export function resumeRestartAndApply(observation: RestartObservation, options: ApplyOptions = {}): Promise<Lifecycle> {
  if (
    !observation.retired.length ||
    observation.retired.some((instance) => !instance.trim()) ||
    !observation.version.trim()
  ) {
    return Promise.reject(new RestartApplyError("configuration", t("lifecycle.missingIdentity")));
  }
  return session(options, (signal) => observe(observation, options, signal));
}

/** Confirm first, preserve work, restart once, then require a different instance with the exact loaded target. */
export function restartAndApply(
  request: {
    confirmed: boolean;
    version?: string;
    settings?: ExpectedSetting[];
    /** Return false when the preserved change does not require replacing this server. */
    preserve?: (signal: AbortSignal) => Promise<boolean | undefined>;
  },
  options: ApplyOptions = {},
): Promise<Lifecycle> {
  if (!request.confirmed) return Promise.reject(new RestartApplyError("confirmation", t("lifecycle.confirmFirst")));
  const requestTimeout = options.requestTimeout ?? 4_000;
  const totalTimeout =
    (options.readinessTimeout ?? 15_000) +
    (request.preserve ? (options.preserveTimeout ?? requestTimeout) : 0) +
    requestTimeout +
    (options.timeout ?? 60_000);
  return session({ ...options, timeout: totalTimeout }, async (signal) => {
    const transport = options.transport ?? lifecycleTransport;
    options.onPhase?.("checking");
    const before = await readyBeforeChange(options, signal);
    const observation: RestartObservation = {
      retired: [before.instance],
      version: request.version ?? before.version,
      settings: structuredClone(request.settings ?? []),
    };
    if (!before.restartable) throw new RestartApplyError("unsupported", t("lifecycle.unsupported"), observation);
    let restartRequired = true;
    if (request.preserve) {
      options.onPhase?.("preserving");
      restartRequired =
        (await bounded<boolean | undefined>(
          (signal) => request.preserve?.(signal) ?? Promise.resolve(undefined),
          signal,
          options.preserveTimeout ?? requestTimeout,
        )) !== false;
    }
    if (!restartRequired) return before;
    options.onObservation?.(observation);
    options.onPhase?.("restarting");
    try {
      const answer = await bounded((signal) => transport.restart(signal), signal, options.requestTimeout ?? 4_000);
      if (answer.restarting !== true || typeof answer.instance !== "string" || !answer.instance.trim())
        throw new Error(t("lifecycle.noAcknowledgement"));
      if (!observation.retired.includes(answer.instance)) observation.retired.push(answer.instance);
    } catch (error) {
      if (!transient(error) && !signal.aborted)
        throw new RestartApplyError(
          "refused",
          error instanceof Error ? error.message : t("lifecycle.refused"),
          observation,
        );
      // A lost acknowledgement has an unknown outcome. Observe; never replay a potentially accepted mutation.
    }
    return observe(observation, options, signal);
  });
}

/** Invalidate only this application's shell; another application may have caches on the same origin. */
export async function clearOwnedShellCaches(
  storage: Pick<CacheStorage, "keys" | "delete"> = window.caches,
): Promise<void> {
  const names = (await storage.keys()).filter((name) => name.startsWith("hz-shell-"));
  await Promise.all(names.map((name) => storage.delete(name)));
}
