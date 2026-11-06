import { useSyncExternalStore } from "react";
import { t } from "@/i18n";
import {
  type ApplyPhase,
  RestartApplyError,
  type RestartObservation,
  restartAndApply,
  resumeRestartAndApply,
} from "@/lifecycle/controller";

export const RECEIPT_STORAGE_KEY = "hz.plugin-add-operation.v1";

export function pluginInstallReceiptExists(): boolean {
  try {
    return window.sessionStorage.getItem(RECEIPT_STORAGE_KEY) !== null;
  } catch {
    return true;
  }
}

export const ACTIVATION_STORAGE_KEY = "hz.plugin-activation.v1";

export function validRestartObservation(value: unknown): value is RestartObservation {
  if (!value || typeof value !== "object") return false;
  const observation = value as Partial<RestartObservation>;
  return (
    Array.isArray(observation.retired) &&
    observation.retired.length > 0 &&
    observation.retired.every((instance) => typeof instance === "string" && instance.trim()) &&
    typeof observation.version === "string" &&
    observation.version.trim().length > 0 &&
    Array.isArray(observation.settings) &&
    observation.settings.every(
      (setting) =>
        setting &&
        typeof setting.section === "string" &&
        typeof setting.key === "string" &&
        Object.hasOwn(setting, "value"),
    )
  );
}

type Activation = {
  phase: ApplyPhase | null;
  pending: boolean;
  error: string | null;
  blocked: boolean;
  changing: boolean;
  observation?: RestartObservation;
};

function restoreActivation(): Activation {
  const idle: Activation = { phase: null, pending: false, error: null, blocked: false, changing: false };
  try {
    const saved = window.sessionStorage.getItem(ACTIVATION_STORAGE_KEY);
    if (saved === null) return idle;
    const record: unknown = JSON.parse(saved);
    if (!record || typeof record !== "object") throw new Error(t("plugins.operationUnavailable"));
    const { acknowledged, observation } = record as { acknowledged?: unknown; observation?: unknown };
    if (acknowledged === true && validRestartObservation(observation) && observation.settings.length === 0)
      return { ...idle, error: t("lifecycle.restartOutcomeUnknown"), observation };
  } catch {
    // Storage failure cannot be treated as evidence that no restart was requested.
  }
  return { ...idle, blocked: true, error: t("plugins.operationUnavailable") };
}

let state = restoreActivation();
let active: AbortController | undefined;
const listeners = new Set<() => void>();

function set(next: Partial<Activation>) {
  state = { ...state, ...next };
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function usePluginActivation() {
  return useSyncExternalStore(subscribe, () => state);
}

export function pluginActivationBlocksChanges() {
  return state.blocked || state.pending || state.observation !== undefined;
}

/** Reserve the restart across the entire operation, including the wait after its package change. */
export function acquirePluginChange(resume?: RestartObservation): (() => void) | null {
  if (state.changing || state.blocked || state.pending || (state.observation && state.observation !== resume))
    return null;
  set({ changing: true });
  return () => {
    set({
      changing: false,
      ...(state.observation ? { error: t("lifecycle.restartOutcomeUnknown") } : {}),
    });
  };
}

export function retainPluginRestart(observation: RestartObservation) {
  window.sessionStorage.setItem(ACTIVATION_STORAGE_KEY, JSON.stringify({ acknowledged: false, observation }));
  set({ observation });
}

export function acknowledgePluginRestart(observation: RestartObservation) {
  window.sessionStorage.setItem(ACTIVATION_STORAGE_KEY, JSON.stringify({ acknowledged: true, observation }));
  set({ observation });
}

export function completePluginRestart() {
  if (!state.observation) return;
  window.sessionStorage.removeItem(ACTIVATION_STORAGE_KEY);
  set({ observation: undefined, error: null });
}

export function stopPluginActivation() {
  active?.abort();
}

/** Keep one observer across footer layout changes and Settings navigation. */
export async function activatePlugins(reload: () => void, canStart: () => boolean) {
  if (state.changing || state.blocked || active || (!state.observation && !canStart())) return;
  active = new AbortController();
  const options = {
    signal: active.signal,
    onPhase: (phase: ApplyPhase) => {
      if (phase !== "applied") set({ phase });
    },
    onObservation: (observation: RestartObservation) => {
      if (!canStart()) throw new Error(t("pluginsPending.finishChange"));
      retainPluginRestart(observation);
    },
    onAcknowledged: acknowledgePluginRestart,
  };
  set({ pending: true, error: null });
  try {
    if (state.observation) await resumeRestartAndApply(state.observation, options);
    else await restartAndApply({ confirmed: true }, options);
    completePluginRestart();
    set({ phase: "applied" });
    reload();
  } catch (error) {
    set({
      error: error instanceof Error ? error.message : t("lifecycle.failed"),
      observation: error instanceof RestartApplyError ? error.observation : state.observation,
    });
  } finally {
    active = undefined;
    set({ pending: false });
  }
}
