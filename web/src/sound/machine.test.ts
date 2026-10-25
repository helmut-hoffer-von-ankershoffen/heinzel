import { afterEach, describe, expect, it, vi } from "vitest";
import {
  along,
  DUCK,
  DUCK_RELEASE_MS,
  DUCK_SECONDS,
  Ducker,
  type Inputs,
  layerGain,
  rampSeconds,
  targetGain,
  why,
} from "./machine";
import { duck, play, resetSound, soundState, stop, toggle, typed } from "./store";

const playing: Inputs = {
  playing: true,
  volume: 80,
  typing: false,
  voice: false,
  duckWhileTyping: true,
  meeting: null,
  muteInMeetings: true,
};

afterEach(() => {
  resetSound();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("how loud the sound plays", () => {
  it("is silent until Play, then at its volume", () => {
    expect(targetGain({ ...playing, playing: false })).toBe(0);
    expect(why({ ...playing, playing: false })).toBe("paused");
    expect(targetGain(playing)).toBeCloseTo(0.8);
    expect(targetGain({ ...playing, volume: 400 })).toBe(1);
  });

  it("ducks while the person types, unless they turned that off, and always while voice records", () => {
    expect(targetGain({ ...playing, typing: true })).toBeCloseTo(0.8 * DUCK);
    expect(why({ ...playing, typing: true })).toBe("ducked");
    expect(targetGain({ ...playing, typing: true, duckWhileTyping: false })).toBeCloseTo(0.8);
    expect(targetGain({ ...playing, voice: true, duckWhileTyping: false })).toBeCloseTo(0.8 * DUCK);
  });

  it("mutes during a meeting when asked to, and an unknown meeting (no calendar) never mutes", () => {
    expect(targetGain({ ...playing, meeting: true })).toBe(0);
    expect(why({ ...playing, meeting: true, typing: true })).toBe("meeting");
    expect(targetGain({ ...playing, meeting: true, muteInMeetings: false })).toBeCloseTo(0.8);
    expect(targetGain({ ...playing, meeting: null })).toBeCloseTo(0.8);
    expect(targetGain({ ...playing, meeting: false })).toBeCloseTo(0.8);
  });

  it("ducks and releases quickly and fades every other change slowly", () => {
    expect(rampSeconds("playing", "ducked", 4)).toBe(DUCK_SECONDS);
    expect(rampSeconds("ducked", "playing", 4)).toBe(DUCK_SECONDS);
    expect(rampSeconds("paused", "playing", 4)).toBe(4);
    expect(rampSeconds("playing", "meeting", 6)).toBe(6);
  });
});

describe("a layer's gain under the signals", () => {
  it("follows the part of the day through its table, a missing part silent", () => {
    const birds = { gain: 0.5, signal: "daypart", curve: { morning: 1, evening: 0.2 } };
    expect(layerGain(birds, { daypart: "morning" })).toBeCloseTo(0.5);
    expect(layerGain(birds, { daypart: "evening" })).toBeCloseTo(0.1);
    expect(layerGain(birds, { daypart: "night" })).toBe(0);
  });

  it("follows a weather signal along its points, and plays at its gain while nobody publishes the signal", () => {
    const rain = {
      gain: 1,
      signal: "weather.rain",
      curve: [
        [0, 0],
        [0.5, 0.8],
        [1, 1],
      ],
    };
    expect(layerGain(rain, { "weather.rain": 0.25 })).toBeCloseTo(0.4);
    expect(layerGain(rain, { "weather.rain": 0.75 })).toBeCloseTo(0.9);
    expect(layerGain(rain, { daypart: "night" })).toBe(1);
    expect(layerGain({ gain: 0.3, signal: "none" }, {})).toBe(0.3);
  });

  it("is flat before the first point and after the last", () => {
    expect(
      along(
        [
          [0.2, 0.1],
          [0.8, 0.9],
        ],
        0,
      ),
    ).toBe(0.1);
    expect(
      along(
        [
          [0.2, 0.1],
          [0.8, 0.9],
        ],
        1,
      ),
    ).toBe(0.9);
  });
});

describe("the ducking state", () => {
  it("starts with a key and releases 1.5 s after the last one", () => {
    vi.useFakeTimers();
    const changes: boolean[] = [];
    const ducker = new Ducker((active) => changes.push(active));
    ducker.key();
    vi.advanceTimersByTime(DUCK_RELEASE_MS - 100);
    ducker.key(); // still typing: no new start, the release moves
    vi.advanceTimersByTime(DUCK_RELEASE_MS - 100);
    expect(changes).toEqual([true]);
    vi.advanceTimersByTime(200);
    expect(changes).toEqual([true, false]);
  });

  it("ducks the store only while sound plays, and voice ducks until it stops", () => {
    vi.useFakeTimers();
    typed();
    expect(soundState().typing).toBe(false); // nothing plays: nothing to duck
    expect(play()).toBe(true);
    typed();
    expect(soundState().typing).toBe(true);
    vi.advanceTimersByTime(DUCK_RELEASE_MS);
    expect(soundState().typing).toBe(false);
    duck("voice", true);
    expect(soundState().voice).toBe(true);
    duck("voice", false);
    expect(soundState().voice).toBe(false);
  });
});

describe("Play", () => {
  it("is refused without a user gesture, so nothing starts on its own", () => {
    vi.stubGlobal("navigator", { ...navigator, userActivation: { isActive: false } });
    expect(play()).toBe(false);
    expect(toggle()).toBe(false);
    expect(soundState()).toMatchObject({ playing: false, started: false });
  });

  it("starts on a gesture, pauses on the next, and stop forgets the player", () => {
    vi.stubGlobal("navigator", { ...navigator, userActivation: { isActive: true } });
    expect(toggle()).toBe(true);
    expect(soundState()).toMatchObject({ playing: true, started: true });
    toggle();
    expect(soundState()).toMatchObject({ playing: false, started: true });
    stop();
    expect(soundState()).toMatchObject({ playing: false, started: false });
  });
});
