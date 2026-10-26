import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const oscillator = () => ({
  type: "sine",
  frequency: { setValueAtTime: vi.fn() },
  connect: vi.fn(),
  start: vi.fn(),
  stop: vi.fn(),
});
const gain = () => ({
  gain: { setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() },
  connect: vi.fn(),
});

class FakeAudioContext {
  state: AudioContextState = "suspended";
  currentTime = 1;
  destination = {} as AudioDestinationNode;
  oscillators: ReturnType<typeof oscillator>[] = [];
  resume = vi.fn(async () => {
    this.state = "running";
  });
  createOscillator() {
    const node = oscillator();
    this.oscillators.push(node);
    return node as unknown as OscillatorNode;
  }
  createGain() {
    return gain() as unknown as GainNode;
  }
}

function installAudio() {
  Object.defineProperty(window, "AudioContext", { configurable: true, value: FakeAudioContext });
  vi.stubGlobal("AudioContext", FakeAudioContext);
}

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

beforeEach(() => vi.resetModules());

describe("notification sound", () => {
  it("is opt-in and plays once per notice ID, even when a stream reconnects", async () => {
    installAudio();
    const sound = await import("./sound");
    expect(sound.notificationSoundEnabled()).toBe(false);
    expect(await sound.dingForNotice("notice-1")).toBe(false);
    expect(await sound.setNotificationSoundEnabled(true)).toBe(true);
    expect(sound.notificationSoundEnabled()).toBe(true);
    expect(await sound.dingForStreamEvent({ event: "changed", latest: "notice-2" })).toBe(false);
    expect(localStorage.getItem("hz.notification-sound.played")).toBeNull();
    expect(await sound.dingForStreamEvent({ event: "notice", notice: { id: "notice-2" } })).toBe(true);
    expect(await sound.dingForNotice("notice-2")).toBe(false);
  });

  it("does not replay an ID already claimed by another tab", async () => {
    installAudio();
    const sound = await import("./sound");
    await sound.setNotificationSoundEnabled(true);
    localStorage.setItem("hz.notification-sound.played", JSON.stringify(["notice-3"]));
    expect(await sound.dingForNotice("notice-3")).toBe(false);
  });

  it("consumes a new notice without sound when browser audio has not been unlocked", async () => {
    installAudio();
    const sound = await import("./sound");
    localStorage.setItem("hz.notification-sound", "on");
    expect(await sound.dingForNotice("notice-4")).toBe(false);
    expect(await sound.primeNotificationSound()).toBe(true);
    expect(await sound.dingForNotice("notice-4")).toBe(false);
  });

  it("does not play in a hidden tab", async () => {
    installAudio();
    const sound = await import("./sound");
    await sound.setNotificationSoundEnabled(true);
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    expect(await sound.dingForNotice("notice-5")).toBe(false);
    expect(localStorage.getItem("hz.notification-sound.played")).toBeNull();
  });

  it("does not enable sound when the browser has no Web Audio support", async () => {
    Object.defineProperty(window, "AudioContext", { configurable: true, value: undefined });
    const sound = await import("./sound");
    expect(sound.notificationSoundAvailable()).toBe(false);
    expect(await sound.setNotificationSoundEnabled(true)).toBe(false);
    expect(sound.notificationSoundEnabled()).toBe(false);
  });
});
