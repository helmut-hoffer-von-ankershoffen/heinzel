import { describe, expect, it } from "vitest";
import {
  FADE_MS,
  fadeMs,
  initial,
  leaveAt,
  MIN_VISIBLE_MS,
  polling,
  reduce,
  type SplashEvent,
  type SplashState,
  visible,
  waiting,
} from "./machine";
import { createSplashStore, unreachableError } from "./store";

const run = (...events: SplashEvent[]): SplashState => events.reduce(reduce, initial);
const booted = run({ type: "mounted" }, { type: "answered" }, { type: "themed" });

describe("the splash's state machine", () => {
  it("starts, connects, signs in and is ready once hz answered and the theme is on the page", () => {
    expect(initial.phase).toBe("starting");
    expect(run({ type: "mounted" }).phase).toBe("connecting");
    expect(run({ type: "mounted" }, { type: "answered" }).phase).toBe("signing-in");
    const ready = run({ type: "mounted" }, { type: "answered" }, { type: "themed" });
    expect(ready.phase).toBe("ready");
    expect(visible(ready)).toBe(false);
  });

  it("signs in while this browser pairs, whatever came first", () => {
    const pairing = run({ type: "pairing" }, { type: "mounted" }, { type: "answered" }, { type: "themed" });
    expect(pairing.phase).toBe("signing-in");
    expect(reduce(pairing, { type: "paired" }).phase).toBe("ready");
  });

  it("keeps connecting while hz doesn't answer, then gives up with the fix", () => {
    const waitingForHz = run({ type: "mounted" }, { type: "unreachable", at: 500 });
    expect(waitingForHz.phase).toBe("connecting");
    expect(waitingForHz.since).toBe(0); // the clock runs from the first paint
    expect(polling(waitingForHz) && waiting(waitingForHz)).toBe(true);
    const gaveUp = reduce(waitingForHz, { type: "timeout" });
    expect([gaveUp.phase, gaveUp.problem]).toEqual(["error", "unreachable"]);
    expect(polling(gaveUp)).toBe(true); // still asking, so it connects as soon as hz is started
    expect(waiting(gaveUp)).toBe(false);
    expect(reduce(reduce(gaveUp, { type: "answered" }), { type: "themed" }).phase).toBe("ready");
  });

  it("shows the token fix when hz refuses this page's token, and stops asking", () => {
    const refused = run({ type: "mounted" }, { type: "refused" });
    expect([refused.phase, refused.problem]).toEqual(["error", "token"]);
    expect(polling(refused)).toBe(false);
    expect(reduce(refused, { type: "timeout" })).toBe(refused);
    expect(reduce(refused, { type: "themed" }).phase).toBe("error");
  });

  it("stops blocking after the timeout when hz answered but something else is slow", () => {
    const slow = run({ type: "mounted" }, { type: "answered" });
    expect(reduce(slow, { type: "timeout" }).phase).toBe("ready");
  });

  it("comes back over the app when hz goes away, from that moment, and leaves when it is back", () => {
    expect(reduce(booted, { type: "suspect" }).checks).toBe(1);
    const down = reduce(booted, { type: "unreachable", at: 42_000 });
    expect([down.phase, down.since]).toEqual(["reconnecting", 42_000]);
    expect(visible(down)).toBe(true);
    expect([reduce(down, { type: "timeout" }).phase, reduce(down, { type: "timeout" }).problem]).toEqual([
      "error",
      "unreachable",
    ]);
    expect(reduce(down, { type: "answered" }).phase).toBe("ready");
  });

  it("says it restarts until the reload, and gives the fix when the restart failed", () => {
    const restarting = reduce(booted, { type: "restart", at: 9_000 });
    expect([restarting.phase, restarting.since]).toEqual(["restarting", 9_000]);
    for (const event of [{ type: "unreachable", at: 9_100 }, { type: "answered" }, { type: "timeout" }] as const) {
      expect(reduce(restarting, event)).toBe(restarting);
    }
    expect(polling(restarting) || waiting(restarting)).toBe(false); // waitForServer asks; the reload ends it
    expect(reduce(restarting, { type: "restart-failed" }).problem).toBe("unreachable");
  });

  it("is never left before its least time, and fades unless the system asks for less motion", () => {
    expect(leaveAt({ ...booted, since: 0 }, 120)).toBe(MIN_VISIBLE_MS);
    expect(leaveAt({ ...booted, since: 0 }, 2_000)).toBe(2_000);
    expect(fadeMs(() => ({ matches: true }))).toBe(0);
    expect(fadeMs(() => ({ matches: false }))).toBe(FADE_MS);
    expect(fadeMs(undefined)).toBe(FADE_MS);
  });

  it("tells subscribers only about changes, and knows which failures mean hz may be gone", () => {
    const store = createSplashStore();
    let calls = 0;
    const stop = store.subscribe(() => {
      calls += 1;
    });
    store.send({ type: "mounted" });
    store.send({ type: "mounted" });
    stop();
    store.send({ type: "answered" });
    expect(calls).toBe(1);
    expect(store.get().phase).toBe("signing-in");
    expect(unreachableError(new TypeError("Failed to fetch"))).toBe(true);
    expect(unreachableError({ status: 502 })).toBe(true);
    expect(unreachableError({ status: 401 })).toBe(false);
    expect(unreachableError(new Error("nope"))).toBe(false);
  });
});
