// Native album downloads are independent of the tab's playback intent.
import { useEffect, useSyncExternalStore } from "react";

/** Host approval binds the selected extended take to these exact bytes. */
export const EXTENDED_VIDEO_SHA = "306c4b7f00c9f89aa80b624f7dcaeace22c52a14ece3b3f7a1e9b107d6c0f126";
export type AlbumMedia = {
  albumID: string | null;
  selectedTrackID: string | null;
  tracks: { id: string; title: string }[];
  extendedVideo: { url: string; sha256: string; trackID: string; admission: "local" | "remote" } | null;
};
export type AlbumSnapshot = {
  media?: AlbumMedia | null;
  volume?: number;
  gain?: number;
  muted?: boolean;
  effectiveGain?: number;
  positionSeconds?: number;
  durationSeconds?: number | null;
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
  | "volume"
  | "catalog"
  | "select"
  | "seek"
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
export type AlbumOptions = AlbumWatchGeometry | { trackID: string; seconds?: number } | { gain: number };
type AlbumBridge = {
  album?: (action: AlbumAction, geometry?: AlbumOptions) => Promise<AlbumSnapshot>;
  clientFlavor?: string;
};
const bridge = () => (window as Window & { heinzelDesktop?: AlbumBridge }).heinzelDesktop;
export const nativeAlbum = (): boolean => typeof bridge()?.album === "function";
export const ALBUM_TITLE = "Dreaming of Electric Snow";

let snapshot: AlbumSnapshot | null = null;
export const albumState = (): AlbumSnapshot | null => snapshot;
let generation = 0;
let dismissed = false;
let pendingActions = 0;

/** Off is authoritative until another intentional transport action. */
export function dismissAlbum(): void {
  dismissed = true;
  ++generation;
  if (snapshot) snapshot = { ...snapshot, playing: false };
  emit();
}
const listeners = new Set<() => void>();
const emit = () => {
  for (const listener of listeners) listener();
};

/** Latest request wins; leaving the album context invalidates pending answers. */
export async function albumAction(action: AlbumAction, geometry?: AlbumOptions): Promise<AlbumSnapshot | undefined> {
  const desktop = bridge();
  if (!desktop?.album) return;
  const passive = action === "status" || action === "catalog";
  if (passive && pendingActions) return;
  if (!passive) pendingActions++;
  if (["select", "play", "toggle", "next", "previous"].includes(action)) dismissed = false;
  if (action === "select" && geometry && "trackID" in geometry && snapshot?.media) {
    snapshot = {
      ...snapshot,
      positionSeconds: 0,
      durationSeconds: null,
      media: { ...snapshot.media, selectedTrackID: geometry.trackID, extendedVideo: null },
    };
    emit();
  }
  const request = ++generation;
  try {
    const answer = await (geometry ? desktop.album(action, geometry) : desktop.album(action));
    if (request === generation && desktop === bridge()) {
      snapshot = dismissed ? { ...answer, playing: false } : answer;
      emit();
    }
    return request === generation ? (snapshot ?? undefined) : undefined;
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
        playing: dismissed ? false : (snapshot?.playing ?? false),
        phase: "failed",
        error: "Album unavailable. Try Play again.",
      };
      emit();
    }
  } finally {
    if (!passive) pendingActions--;
  }
}

export function leaveAlbum(): void {
  dismissed = false;
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
export function useAlbumPosition(awaitingDuration: boolean, playing: boolean) {
  useEffect(() => {
    if (!awaitingDuration && (!playing || bridge()?.clientFlavor === "electron")) return;
    const timer = window.setInterval(change, 1000);
    return () => window.clearInterval(timer);
  }, [awaitingDuration, playing]);
}

export function useAlbum() {
  useEffect(() => {
    void albumAction("status");
  }, []);
  return useSyncExternalStore(subscribe, () => snapshot);
}
