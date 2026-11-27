// Native album downloads are independent of the tab's playback intent.
import { useEffect, useSyncExternalStore } from "react";

export type AlbumSnapshot = {
  phase: "idle" | "downloading" | "paused" | "cancelled" | "ready" | "failed";
  verified: number;
  total: number;
  verifiedBytes: number;
  receivedBytes?: number;
  totalBytes: number;
  error: string | null;
  title: string;
  playing: boolean;
  repeatMode: "Off" | "Album" | "Track";
};

export type AlbumAction =
  | "status"
  | "play"
  | "pausePlayback"
  | "pauseDownload"
  | "resumeDownload"
  | "cancelDownload"
  | "previous"
  | "next"
  | "toggle"
  | "repeat"
  | "picker"
  | "openVideo";

export type AlbumWatchGeometry = { width: number; viewportWidth: number };
type AlbumBridge = { album?: (action: AlbumAction, geometry?: AlbumWatchGeometry) => Promise<AlbumSnapshot> };
const bridge = () => (window as Window & { heinzelDesktop?: AlbumBridge }).heinzelDesktop;
export const nativeAlbum = (): boolean => typeof bridge()?.album === "function";
export const ALBUM_TITLE = "Dreaming of Electric Snow";

let snapshot: AlbumSnapshot | null = null;
export const albumState = (): AlbumSnapshot | null => snapshot;
let generation = 0;
const listeners = new Set<() => void>();
const emit = () => {
  for (const listener of listeners) listener();
};

/** Latest request wins; leaving the album context invalidates pending answers. */
export async function albumAction(action: AlbumAction, geometry?: AlbumWatchGeometry): Promise<AlbumSnapshot | undefined> {
  const desktop = bridge();
  if (!desktop?.album) return;
  const request = ++generation;
  try {
    const answer = await (geometry ? desktop.album(action, geometry) : desktop.album(action));
    if (request === generation && desktop === bridge()) {
      snapshot = answer;
      emit();
    }
    return answer;
  } catch {
    if (request === generation && desktop === bridge()) {
      snapshot = {
        ...(snapshot ?? {
          verified: 0,
          total: 0,
          verifiedBytes: 0,
          totalBytes: 0,
          title: "",
          playing: false,
          repeatMode: "Off" as const,
        }),
        phase: "failed",
        error: "Album unavailable. Try Play again.",
      };
      emit();
    }
  }
}

export function leaveAlbum(): void {
  ++generation;
  snapshot = null;
  emit();
}

const change = () => {
  void albumAction("status");
};
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  if (listeners.size === 1) window.addEventListener("heinzel-album-change", change);
  // Each subscriber has a stable listener; remove the event with the last subscription.
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      ++generation;
      window.removeEventListener("heinzel-album-change", change);
    }
  };
};
export function useAlbum() {
  useEffect(() => {
    void albumAction("status");
  }, []);
  return useSyncExternalStore(subscribe, () => snapshot);
}
