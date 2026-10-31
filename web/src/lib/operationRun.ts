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
  | { stage: "running"; call: SchemaOperationCall; phases: Phase[]; task: string | null }
  | { stage: "done"; call: SchemaOperationCall; phases: Phase[]; result: unknown }
  | { stage: "failed"; call: SchemaOperationCall; phases: Phase[]; error: string };

/** What a head reports for one step; anything else in a `phase` event is ignored. */
export function parsePhase(data: Record<string, unknown>): Phase | null {
  const states: PhaseState[] = ["pending", "running", "done", "warn", "skipped", "failed"];
  const { id, label, state, detail } = data;
  if (typeof id !== "string" || typeof label !== "string" || !states.includes(state as PhaseState)) return null;
  return { id, label, state: state as PhaseState, ...(typeof detail === "string" && detail ? { detail } : {}) };
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
  return () => void listeners.delete(listener);
};

export const useOperationRun = (): OperationRun => useSyncExternalStore(subscribe, () => current);

/** Ask the person to confirm `call`; nothing runs yet. Ignored while another write is being asked about or running. */
export function requestOperation(call: SchemaOperationCall): void {
  if (current.stage !== "idle" && current.stage !== "done" && current.stage !== "failed") return;
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  set({ stage: "confirm", call });
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
  set({ stage: "running", call, phases, task: null });
  try {
    const result = await stream<unknown>(
      call.path,
      call.query,
      ({ event, data }) => {
        if (current.stage !== "running") return;
        if (event === "phase") {
          const phase = parsePhase(data);
          if (!phase) return;
          const known = phases.some((entry) => entry.id === phase.id);
          phases = known
            ? phases.map((entry) => (entry.id === phase.id ? { ...entry, ...phase } : entry))
            : [...phases, phase];
          set({ stage: "running", call, phases, task: null });
        } else if (event === "task" && typeof data.text === "string") {
          set({ stage: "running", call, phases, task: data.text });
        }
      },
      undefined,
      call.body,
    );
    set({ stage: "done", call, phases: settle(phases), result });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    set({ stage: "failed", call, phases, error: message });
  } finally {
    onSettled();
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
      return () => void hostListeners.delete(listener);
    },
    () => hosts[0] === id,
  );
}
