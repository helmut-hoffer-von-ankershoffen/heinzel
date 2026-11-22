// An audio element's playback for tests (jsdom plays nothing): play and pause flip `paused` and send their events, and
// a test ends or fails a track by hand. Media Session is recorded, so a test can press the lock screen's buttons.
import { vi } from "vitest";

export function fakeMedia() {
  const states = new WeakMap<HTMLMediaElement, { paused: boolean; ended: boolean; error: object | null; at: number }>();
  const of = (element: HTMLMediaElement) => {
    const state = states.get(element) ?? { paused: true, ended: false, error: null, at: 0 };
    states.set(element, state);
    return state;
  };
  const prototype = HTMLMediaElement.prototype;
  // jsdom has only some of these properties, so each is defined rather than spied on, and put back after.
  const originals = new Map<string, PropertyDescriptor | undefined>();
  const fake = (name: string, descriptor: PropertyDescriptor & ThisType<HTMLMediaElement>) => {
    originals.set(name, Object.getOwnPropertyDescriptor(prototype, name));
    Object.defineProperty(prototype, name, { configurable: true, ...descriptor });
  };
  fake("paused", {
    get() {
      return of(this).paused;
    },
  });
  fake("ended", {
    get() {
      return of(this).ended;
    },
  });
  fake("error", {
    get() {
      return of(this).error;
    },
  });
  fake("currentTime", {
    get() {
      return of(this).at;
    },
    set(at: number) {
      of(this).at = at;
    },
  });
  fake("src", {
    get() {
      return this.getAttribute("src") ?? "";
    },
    set(url: string) {
      // A new source starts the load algorithm: paused, not ended, no error, at the start.
      states.set(this, { paused: true, ended: false, error: null, at: 0 });
      this.setAttribute("src", url);
    },
  });
  const load = vi.spyOn(prototype, "load").mockImplementation(() => undefined);
  const play = vi.spyOn(prototype, "play").mockImplementation(function (this: HTMLMediaElement) {
    of(this).paused = false;
    this.dispatchEvent(new Event("playing"));
    return Promise.resolve();
  });
  const pause = vi.spyOn(prototype, "pause").mockImplementation(function (this: HTMLMediaElement) {
    if (of(this).paused) return;
    of(this).paused = true;
    this.dispatchEvent(new Event("pause"));
  });

  const handlers = new Map<string, MediaSessionActionHandler>();
  const session = {
    metadata: null as { title: string; album: string; artwork: { src: string }[] } | null,
    playbackState: "none",
    setActionHandler: vi.fn((action: string, handler: MediaSessionActionHandler | null) => {
      if (handler) handlers.set(action, handler);
      else handlers.delete(action);
    }),
  };
  Object.defineProperty(navigator, "mediaSession", { configurable: true, value: session });
  vi.stubGlobal(
    "MediaMetadata",
    class {
      constructor(init: object) {
        Object.assign(this, init);
      }
    },
  );

  return {
    play,
    pause,
    load,
    session,
    handlers,
    press: (action: MediaSessionAction) => handlers.get(action)?.({ action }),
    /** The track reached its end, as a browser says it: paused and ended, `pause`, then `ended`. */
    end(element: HTMLMediaElement) {
      Object.assign(of(element), { paused: true, ended: true });
      element.dispatchEvent(new Event("pause"));
      element.dispatchEvent(new Event("ended"));
    },
    fail(element: HTMLMediaElement, at = 0) {
      Object.assign(of(element), { error: { code: 2 }, at });
      element.dispatchEvent(new Event("error"));
    },
    /** The system paused it: a call, or headphones pulled out. */
    interrupt(element: HTMLMediaElement) {
      of(element).paused = true;
      element.dispatchEvent(new Event("pause"));
    },
    /** The system resumed it by itself, as after a call. */
    resume(element: HTMLMediaElement) {
      of(element).paused = false;
      element.dispatchEvent(new Event("play"));
    },
    withoutSession() {
      Reflect.deleteProperty(navigator, "mediaSession");
    },
    restore() {
      Reflect.deleteProperty(navigator, "mediaSession");
      for (const [name, original] of originals) {
        if (original) Object.defineProperty(prototype, name, original);
        else Reflect.deleteProperty(prototype, name);
      }
    },
  };
}
