// What Heinzel itself plays, for the audio visualizer: no permission and no browser sharing bar. Layered soundscapes
// publish the analyser of their own mix. A stream in its host's cross-origin frame cannot be tapped by any in-page
// API, so it is published as "frame": the visualizer then needs the microphone or a tab capture for the real signal.
import { useSyncExternalStore } from "react";

export type OwnAudio =
  | { mode: "none" }
  | { mode: "analysed"; analyser: AnalyserNode; label: string }
  | { mode: "frame"; label: string };

const none: OwnAudio = { mode: "none" };
let current: OwnAudio = none;
const listeners = new Set<() => void>();

export function publishOwnAudio(next: OwnAudio): void {
  current = next;
  for (const listener of listeners) listener();
}

export function useOwnAudio(): OwnAudio {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
  );
}
