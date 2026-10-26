// The little idle moves of a living Heinzelmännchen (a blink, a hat tilt, a wave, a glance, a hop), one at a random
// moment every 8 to 30 seconds. Never when the person prefers reduced motion, when `playful` is off (the caller's
// `enabled`) or while the tab is hidden; the schedule stops then and starts again when the gates open.
import { useEffect, useState } from "react";

export const IDLE_MOVES = ["blink", "tilt", "wave", "glance-left", "glance-right", "hop"] as const;
export type IdleMove = (typeof IDLE_MOVES)[number];

/** The shortest and longest wait between two moves, in milliseconds. */
export const IDLE_MIN_MS = 8_000;
export const IDLE_MAX_MS = 30_000;
/** How long a move shows (its CSS animation is shorter), then the figure is at rest again. */
export const IDLE_SHOW_MS = 1_400;

const REDUCED = "(prefers-reduced-motion: reduce)";

/** Whether motion is welcome right now: not reduced, tab visible. */
export function motionAllowed(): boolean {
  const reduced = typeof window.matchMedia === "function" && window.matchMedia(REDUCED).matches;
  return !reduced && document.visibilityState !== "hidden";
}

/** Pick a move without immediately repeating the previous one when another choice exists. */
export function nextIdleMove(
  moves: readonly IdleMove[],
  previous: IdleMove | null,
  random = Math.random,
): IdleMove | null {
  const choices = moves.length > 1 ? moves.filter((move) => move !== previous) : moves;
  return choices[Math.floor(random() * choices.length)] ?? null;
}

/** The move being played, or null at rest. `moves` is what this view can show (a head has no hand to wave). */
export function useIdleLife(enabled: boolean, moves: readonly IdleMove[] = IDLE_MOVES): IdleMove | null {
  const [move, setMove] = useState<IdleMove | null>(null);
  const available = moves.join(",");
  useEffect(() => {
    if (!enabled) return;
    const choices = available.split(",") as IdleMove[];
    let previous: IdleMove | null = null;
    let wait: ReturnType<typeof setTimeout> | undefined;
    let show: ReturnType<typeof setTimeout> | undefined;
    const stop = () => {
      clearTimeout(wait);
      clearTimeout(show);
      setMove(null);
    };
    const schedule = () => {
      wait = setTimeout(
        () => {
          previous = nextIdleMove(choices, previous);
          setMove(previous);
          show = setTimeout(() => setMove(null), IDLE_SHOW_MS);
          schedule();
        },
        IDLE_MIN_MS + Math.random() * (IDLE_MAX_MS - IDLE_MIN_MS),
      );
    };
    const sync = () => {
      stop();
      if (motionAllowed()) schedule();
    };
    const query = typeof window.matchMedia === "function" ? window.matchMedia(REDUCED) : undefined;
    sync();
    document.addEventListener("visibilitychange", sync);
    query?.addEventListener?.("change", sync);
    return () => {
      document.removeEventListener("visibilitychange", sync);
      query?.removeEventListener?.("change", sync);
      stop();
    };
  }, [enabled, available]);
  return enabled ? move : null;
}
