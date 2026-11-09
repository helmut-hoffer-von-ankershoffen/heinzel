// One write the app runs on the person's confirmation and shows while it happens (ADR 0056): the confirmation, the
// real steps the operation reports (`phase` events of `POST /api/stream/<path>`, ADR 0045), and how it ended. The
// state lives here, not in a component, so a footer that re-lays itself out cannot lose a running install.
import { useEffect, useId, useSyncExternalStore } from "react";
import type { SchemaOperationCall } from "@/api/client";
import { stream } from "@/api/stream";

export type PhaseState = "pending" | "running" | "done" | "warn" | "skipped" | "failed";
export type Phase = { id: string; label: string; state: PhaseState; detail?: string };
export type OperationRun =
  | { stage: "idle" }
  | { stage: "confirm"; call: SchemaOperationCall }
  | { stage: "running"; call: SchemaOperationCall; phases: Phase[]; task: string | null; tracked?: true }
  | { stage: "done"; call: SchemaOperationCall; phases: Phase[]; result: unknown; tracked?: true }
  | {
      stage: "failed";
      call: SchemaOperationCall;
      phases: Phase[];
      error: string;
      tracked?: true;
      outcomeUnknown?: true;
    };

/** What a head reports for one step; anything else in a `phase` event is ignored. */
export function parsePhase(data: Record<string, unknown>): Phase | null {
  const states: PhaseState[] = ["pending", "running", "done", "warn", "skipped", "failed"];
  const { id, label, state, detail } = data;
  if (typeof id !== "string" || typeof label !== "string" || !states.includes(state as PhaseState)) return null;
  return { id, label, state: state as PhaseState, ...(typeof detail === "string" && detail ? { detail } : {}) };
}

/** `phases` with `phase` told: a later report of a known step replaces it in place, a new one goes last. */
export function mergePhase(phases: Phase[], phase: Phase): Phase[] {
  return phases.some((entry) => entry.id === phase.id)
    ? phases.map((entry) => (entry.id === phase.id ? { ...entry, ...phase } : entry))
    : [...phases, phase];
}

let current: OperationRun = { stage: "idle" };
let opener: HTMLElement | null = null;
const listeners = new Set<() => void>();
const set = (next: OperationRun) => {
  current = next;
  for (const listener of listeners) listener();
};
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};

export const useOperationRun = (): OperationRun => useSyncExternalStore(subscribe, () => current);

/** Ask the person to confirm `call`; nothing runs yet. Ignored while another write is being asked about or running. */
export function requestOperation(call: SchemaOperationCall): boolean {
  if (current.stage !== "idle" && current.stage !== "done" && current.stage !== "failed") return false;
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  set({ stage: "confirm", call });
  return true;
}

/** Ask the person to confirm `call` and settle with its result; fails when they cancel or it ends failed. */
export function confirmOperation(call: SchemaOperationCall): Promise<unknown> {
  if (!requestOperation(call)) return Promise.reject(new Error("Another change is being confirmed or running"));
  return new Promise((resolve, reject) => {
    let error = "Cancelled";
    const listener = () => {
      if (current.stage === "done" && current.call === call) {
        listeners.delete(listener);
        resolve(current.result);
        return;
      }
      if (current.stage === "failed" && current.call === call) error = current.error;
      else if (current.stage === "idle" || current.call !== call) {
        listeners.delete(listener);
        reject(new Error(error));
      }
    };
    listeners.add(listener);
  });
}

/** Close the confirmation, or the outcome; a running operation can't be closed, it can't be stopped safely. */
export function dismissOperation(): void {
  if (current.stage === "running") return;
  set({ stage: "idle" });
  // The focus goes back to the button that asked.
  const back = opener;
  opener = null;
  window.setTimeout(() => back?.isConnected && back.focus());
}

/** Start the confirmed write. A second call while it runs does nothing: one click starts one operation. */
export async function startOperation(onSettled: () => void): Promise<void> {
  if (current.stage !== "confirm" && current.stage !== "failed") return;
  const { call } = current;
  let phases: Phase[] = [];
  const timeout = call.path === "/system/daemon/restart" ? AbortSignal.timeout(30_000) : undefined;
  let confirmedFailure = false;
  set({ stage: "running", call, phases, task: null });
  try {
    const result = await stream<unknown>(
      call.path,
      call.query,
      ({ event, data }) => {
        if (current.stage !== "running") return;
        if (event === "error") confirmedFailure = true;
        if (event === "phase") {
          const phase = parsePhase(data);
          if (!phase) return;
          phases = mergePhase(phases, phase);
          set({ stage: "running", call, phases, task: null });
        } else if (event === "task" && typeof data.text === "string") {
          set({ stage: "running", call, phases, task: data.text });
        }
      },
      timeout,
      call.body,
    );
    // A restart may return normally without restarting when its service definition is absent.
    if (
      call.path === "/system/daemon/restart" &&
      typeof result === "object" &&
      result !== null &&
      "restarted" in result &&
      result.restarted === false
    ) {
      confirmedFailure = true;
      throw new Error("The daemon was not restarted.");
    }
    set({ stage: "done", call, phases: settle(phases), result });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    set({
      stage: "failed",
      call,
      phases,
      error: message,
      ...(timeout && !confirmedFailure ? { outcomeUnknown: true } : {}),
    });
  } finally {
    onSettled();
  }
}

/**
 * Show a write that another part of the app drives in the same dialog, with the steps it reports (`report` takes the
 * whole list each time): the update installs, restarts and reloads, so it can't be one streamed call. The person
 * already confirmed it where they asked; it is shown, never started or retried, here. Resolves or rejects as `run`.
 */
export async function trackOperation<T>(
  call: SchemaOperationCall,
  run: (report: (phases: Phase[]) => void) => Promise<T>,
): Promise<T> {
  if (current.stage === "confirm" || current.stage === "running") throw new Error("Another write is running.");
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  let phases: Phase[] = [];
  set({ stage: "running", call, phases, task: null, tracked: true });
  try {
    const result = await run((next) => {
      phases = next;
      if (current.stage === "running" && current.call === call)
        set({ stage: "running", call, phases, task: null, tracked: true });
    });
    set({ stage: "done", call, phases: settle(phases), result, tracked: true });
    return result;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    set({ stage: "failed", call, phases, error: message, tracked: true });
    throw error;
  }
}

/** A finished run has no step still running or waiting: whatever the head didn't finish is marked as it was. */
const settle = (phases: Phase[]): Phase[] =>
  phases.map((phase) =>
    phase.state === "running" || phase.state === "pending" ? { ...phase, state: "skipped" } : phase,
  );

/** The state now, for tests. */
export const getRun = (): OperationRun => current;

/** Reset for tests. */
export function resetOperationRun(): void {
  set({ stage: "idle" });
}

const hosts: string[] = [];
const hostListeners = new Set<() => void>();

/** Whether this component is the first mounted dialog host: only that one draws the dialog, whatever else mounts. */
export function usePrimaryHost(): boolean {
  const id = useId();
  useEffect(() => {
    hosts.push(id);
    for (const listener of hostListeners) listener();
    return () => {
      hosts.splice(hosts.indexOf(id), 1);
      for (const listener of hostListeners) listener();
    };
  }, [id]);
  return useSyncExternalStore(
    (listener) => {
      hostListeners.add(listener);
      return () => {
        hostListeners.delete(listener);
      };
    },
    () => hosts[0] === id,
  );
}
