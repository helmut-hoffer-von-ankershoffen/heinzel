// What the sound does in this tab (ADR 0058): played or paused, ducked, and why it can't play. One store for the
// whole app, so the sidebar's button, the settings and the engine that plays agree without a provider.
// Play needs a gesture: browsers allow audio only after one, and the person decides when a stream's host is contacted.
import { useSyncExternalStore } from "react";
import { Ducker } from "./machine";

export type SoundState = {
  /** Pressed Play and not Pause since; never true without a gesture. */
  playing: boolean;
  /** Ever pressed Play in this tab for the soundscape in effect: a stream's player exists only from then on. */
  started: boolean;
  /** The soundscape Play was pressed for; null when it was pressed for "whatever is in effect". */
  soundscape: string | null;
  typing: boolean;
  voice: boolean;
  /** Why the sound can't play, in words; null when it can. */
  problem: string | null;
};

const initial: SoundState = {
  playing: false,
  started: false,
  soundscape: null,
  typing: false,
  voice: false,
  problem: null,
};
let state = initial;
const listeners = new Set<() => void>();

function set(change: Partial<SoundState>): void {
  state = { ...state, ...change };
  for (const listener of listeners) listener();
}

/** The sound's state in this tab, following every change. */
export function useSound(): SoundState {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => state,
  );
}

export const soundState = (): SoundState => state;

/** Whether this call comes from a user gesture: browsers that can tell say so; others trust the caller's click. */
function gesture(): boolean {
  const activation = (navigator as Navigator & { userActivation?: { isActive: boolean } }).userActivation;
  return activation ? activation.isActive : true;
}

/**
 * Play, from a click or a key press only; refused (false) otherwise, so nothing starts on its own. Given the
 * soundscape it is for, a later change of the soundscape in effect to that one doesn't stop it: that is a pick that
 * plays (the engine stops what plays only when another soundscape than the one pressed for comes into effect).
 */
export function play(soundscape?: string): boolean {
  if (!gesture()) return false;
  set({ playing: true, started: true, soundscape: soundscape ?? null, problem: null });
  return true;
}

/**
 * Play again after the page reloaded for a pick (the frame origin of a stream changed): the gesture was the click
 * before the reload, which `takeResume` vouches for. The browser's own autoplay rules still apply to what plays.
 */
export function resume(soundscape: string): void {
  set({ playing: true, started: true, soundscape, problem: null });
}

export function pause(): void {
  set({ playing: false });
}

/** Stop and forget the player: a stream's frame is removed, layered audio released. */
export function stop(): void {
  ducker.stop();
  set({ playing: false, started: false, soundscape: null });
}

export function toggle(soundscape?: string): boolean {
  if (state.playing) {
    pause();
    return true;
  }
  return play(soundscape);
}

/** Stop with a reason; the next Play starts afresh. */
export function fail(problem: string): void {
  ducker.stop();
  set({ playing: false, started: false, soundscape: null, problem });
}

const ducker = new Ducker((typing) => set({ typing }));

/** Duck the sound for a while: call it on every key typed in the chat; it releases 1.5 s after the last. */
export function typed(): void {
  if (state.playing) ducker.key();
}

/** Duck the sound while voice input records (`duck("voice", true)`) until it stops (`duck("voice", false)`). */
export function duck(source: "voice", on: boolean): void {
  if (source === "voice") set({ voice: on });
}

/** For tests: start from nothing. */
export function resetSound(): void {
  ducker.stop();
  state = initial;
  for (const listener of listeners) listener();
}
