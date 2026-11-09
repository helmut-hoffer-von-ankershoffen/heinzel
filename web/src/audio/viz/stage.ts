// Where the Heinzel styles (Halo and Swarm) stand: the sidebar's gap above the player, handed over by the sidebar with
// whether the Heinzels play along (Settings > General, "playful"; off hides them like every Gnome in the app).
import { useSyncExternalStore } from "react";

export type VizStage = { readonly element: HTMLElement; readonly playful: boolean } | null;

let stage: VizStage = null;
const listeners = new Set<() => void>();

export function setVizStage(next: VizStage): void {
  if (stage?.element === next?.element && stage?.playful === next?.playful) return;
  stage = next;
  for (const listener of listeners) listener();
}

export const useVizStage = (): VizStage =>
  useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => stage,
  );
