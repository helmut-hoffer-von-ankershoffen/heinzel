import { afterEach, describe, expect, it, vi } from "vitest";
import { type FakeContext, FakeParam, fakeAudio } from "@/test/fakeAudio";
import { MAX_LAYER_SECONDS, Mixer, ramp } from "./mixer";

/** `fetch` answers each URL with its own path as the body, which the fake decoder reads to pick a duration. */
function serveUrls(status = 200) {
  const fetched: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: URL) => {
      fetched.push(input.pathname);
      return new Response(input.pathname, { status });
    }),
  );
  return fetched;
}

const rain = { url: "/sound-assets/rain/layer-0.ogg?v=1", gain: 0.5, signal: "daypart", curve: { morning: 1 } };

afterEach(() => vi.unstubAllGlobals());

function mixer(seconds?: (url: string) => number): [Mixer, FakeContext] {
  const { Context, contexts } = fakeAudio(seconds);
  const made = new Mixer(new Context());
  return [made, contexts[0] as FakeContext];
}

describe("the mixer", () => {
  it("loops each layer through its own gain under the signals, silent until it fades in", async () => {
    const fetched = serveUrls();
    const [made, context] = mixer();
    await made.load([rain], [], { daypart: "morning" });
    expect(fetched).toEqual(["/sound-assets/rain/layer-0.ogg"]);
    const [master, layer] = context.gains;
    expect(master?.gain.value).toBe(0);
    expect(layer?.gain.value).toBe(0.5);
    expect(context.sources[0]).toMatchObject({ loop: true });
    expect(context.sources[0]?.start).toHaveBeenCalled();
    made.fade(0.4, 3);
    expect(master?.gain.setTargetAtTime).toHaveBeenCalledWith(0.4, 0, 1);
    made.follow({ daypart: "evening" }, 6);
    expect(layer?.gain.target).toBe(0); // evening isn't in the curve: silent
    await made.close();
    expect(context.sources[0]?.stop).toHaveBeenCalled();
    expect(context.close).toHaveBeenCalled();
  });

  it("refuses a layer that decodes longer than two minutes, or one hz doesn't serve", async () => {
    serveUrls();
    await expect(mixer(() => MAX_LAYER_SECONDS + 1)[0].load([rain], [], {})).rejects.toThrow("longer than 120 s");
    serveUrls(404);
    await expect(mixer()[0].load([rain], [], {})).rejects.toThrow("HTTP 404");
  });

  it("rings an earcon of a notice's kind once, and an earcon too long or unknown not at all", async () => {
    serveUrls();
    const [made, context] = mixer((url) => (url.includes("failed") ? 30 : 1));
    const earcons = [
      { kind: "draft", url: "/sound-assets/rain/earcon-draft.ogg" },
      { kind: "failed", url: "/sound-assets/rain/earcon-failed.ogg" },
    ];
    await made.load([rain], earcons, {});
    made.earcon("draft");
    made.earcon("failed");
    made.earcon("release");
    expect(context.sources.length).toBe(2); // the layer, and the draft's earcon
    expect(context.sources[1]?.start).toHaveBeenCalled();
  });
});

describe("a ramp", () => {
  it("starts from where the gain is and gets about 95% there in the given seconds", () => {
    const param = new FakeParam();
    param.value = 0.2;
    ramp(param as unknown as AudioParam, 0.8, 3, 5);
    expect(param.cancelScheduledValues).toHaveBeenCalledWith(5);
    expect(param.setValueAtTime).toHaveBeenCalledWith(0.2, 5);
    expect(param.setTargetAtTime).toHaveBeenCalledWith(0.8, 5, 1);
    ramp(param as unknown as AudioParam, 0, 0, 5);
    expect(param.setTargetAtTime).toHaveBeenLastCalledWith(0, 5, 0.01 / 3);
  });
});
