import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SchemaSoundList, SchemaSoundscapeView } from "@/api/client";
import { type FakeContext, fakeAudio } from "@/test/fakeAudio";
import { acmeTheme, shell, themeList } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { needsReload, page, pendingResume, rememberPageFrame, resetPageFrame } from "./queries";
import { duck, resetSound, soundState } from "./store";
import { rememberYouTubeAccount } from "./stream";

const ORIGIN = "https://www.youtube-nocookie.com";

const off: SchemaSoundscapeView = {
  name: "off",
  title: "Off",
  kind: "off",
  module: null,
  plugin: null,
  selected: true,
  effective: true,
  fade_seconds: 4,
};
const fm: SchemaSoundscapeView = {
  name: "fixture-radio",
  title: "Fixture radio",
  kind: "stream",
  module: "system.sound",
  plugin: "heinzel",
  selected: false,
  effective: false,
  fade_seconds: 4,
  stream: {
    provider: "youtube",
    video: "abcdefghijk",
    embed_url: `${ORIGIN}/embed/abcdefghijk`,
    frame_origin: ORIGIN,
    watch_url: "https://www.youtube.com/watch?v=abcdefghijk",
  },
};
const rain: SchemaSoundscapeView = {
  name: "rain",
  title: "Rain",
  kind: "layers",
  module: "rain",
  plugin: "heinzel-rain",
  selected: false,
  effective: false,
  fade_seconds: 4,
  layers: [
    {
      url: "/sound-assets/rain/layer-0.ogg?v=1",
      licence: "CC-BY-4.0",
      attribution: "Jo Example",
      source: null,
      gain: 1,
      signal: "none",
      curve: null,
    },
  ],
};

function soundList(selected: string, overrides: Partial<SchemaSoundList> = {}): SchemaSoundList {
  return {
    selected,
    effective: selected,
    soundscapes: [off, fm, rain].map((s) => ({ ...s, selected: s.name === selected, effective: s.name === selected })),
    signals: { daypart: "morning" },
    volume: 50,
    follow_theme: false,
    mute_in_meetings: true,
    duck_while_typing: true,
    ...overrides,
  };
}

/** The API with a sound list that follows `hz system sound use`. */
function soundApi(initial: string, picked?: string) {
  let selected = initial;
  return mockApi({
    ...shell,
    get "/api/system/sound/list"() {
      return soundList(selected);
    },
    get "/api/system/sound/use"() {
      selected = picked ?? selected;
      return soundList(selected);
    },
  });
}

/** Which speaker the sidebar's button shows: lucide marks its icons by class. */
const speakerIcon = () => {
  const icon = screen.getByRole("button", { name: /^Choose a soundscape$/ }).querySelector("svg");
  return [...(icon?.classList ?? [])].find((name) => /^lucide-volume/.test(name))?.replace("lucide-", "");
};

/** The layers' files, for a pick that plays at once: any answer decodes in the fake audio. */
function serveLayers() {
  const answer = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | URL) => {
      const url = input instanceof URL ? input : new URL(input.url);
      return url.pathname.startsWith("/sound-assets/") ? new Response(url.pathname) : answer(input as Request);
    }),
  );
}

afterEach(() => {
  sessionStorage.clear();
  resetSound();
  resetPageFrame();
  vi.restoreAllMocks();
});

const speaker = (name: RegExp) => screen.findByRole("button", { name });
const openSoundChoices = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(await speaker(/^Choose a soundscape$/));
  return screen.findByRole("dialog", { name: "Sound" });
};
const playSound = async (user: ReturnType<typeof userEvent.setup>, title: string) => {
  const dialog = screen.queryByRole("dialog", { name: "Sound" }) ?? (await openSoundChoices(user));
  await user.click(within(dialog).getByRole("button", { name: `Play ${title}` }));
};

describe("Sound", () => {
  it.each(["fixture-radio", "rain"])("offers working volume controls for %s on iPhone", async (name) => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (iPhone) AppleWebKit/605.1.15");
    soundApi(name);
    const user = userEvent.setup();
    renderAt("/");
    const dialog = await openSoundChoices(user);
    if (name === "fixture-radio") {
      expect(within(dialog).getByText(/Use your iPhone or iPad volume buttons/)).toBeInTheDocument();
      expect(within(dialog).queryByRole("slider", { name: "Volume" })).not.toBeInTheDocument();
      expect(within(dialog).queryByRole("switch", { name: "Softer while I type" })).not.toBeInTheDocument();
    } else {
      expect(within(dialog).getByRole("slider", { name: "Volume" })).toHaveValue("50");
      expect(within(dialog).getByRole("switch", { name: "Softer while I type" })).toBeInTheDocument();
    }
  });

  it("switches from the followed theme sound to a manual contributor soundscape and plays its source", async () => {
    vi.spyOn(page, "reload").mockImplementation(() => undefined);
    const snow: SchemaSoundscapeView = {
      ...rain,
      name: "snow",
      title: "Snow",
      plugin: "heinzel-snow",
      pairs_with: ["winter"],
      layers: [
        {
          url: "/sound-assets/snow/layer-0.ogg?v=1",
          licence: "CC-BY-4.0",
          attribution: "Jo Example",
          source: null,
          gain: 1,
          signal: "none",
          curve: null,
        },
      ],
    };
    const soundscapes = [off, fm, rain, snow];
    let manual = false;
    const list = (): SchemaSoundList => ({
      ...soundList(manual ? "rain" : "off", {
        selected: manual ? "rain" : "off",
        effective: manual ? "rain" : "snow",
        follow_theme: !manual,
      }),
      soundscapes: soundscapes.map((s) => ({
        ...s,
        selected: s.name === (manual ? "rain" : "off"),
        effective: s.name === (manual ? "rain" : "snow"),
      })),
    });
    const api = mockApi({
      ...shell,
      get "/api/system/sound/list"() {
        return list();
      },
      get "/api/system/sound/use"() {
        manual = true;
        return list();
      },
    });
    const { Context, contexts } = fakeAudio();
    vi.stubGlobal("AudioContext", Context);
    const answer = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request | URL) => {
        const url = input instanceof URL ? input : new URL(input.url);
        if (url.pathname.startsWith("/sound-assets/")) {
          api.calls.push(new Request(url));
          return new Response(url.pathname);
        }
        return answer(input as Request);
      }),
    );

    const user = userEvent.setup();
    renderAt("/");
    const snowSpeaker = await speaker(/^Choose a soundscape$/);
    await playSound(user, "Snow");
    await waitFor(() =>
      expect(api.calls.some((call) => call.url.includes("/sound-assets/snow/layer-0.ogg"))).toBe(true),
    );
    expect(contexts).toHaveLength(1);
    expect(contexts[0]?.sources).toHaveLength(1);
    expect(soundState()).toMatchObject({ playing: true, started: true });

    const dialog = await screen.findByRole("dialog", { name: "Sound" });
    expect(within(dialog).getByRole("switch", { name: "Follow my theme" })).toHaveAttribute("aria-checked", "true");
    // Picking Rain keeps the picker open, switches over and plays it without a second click.
    await user.click(within(dialog).getByRole("radio", { name: /^Rain/ }));
    await waitFor(() =>
      expect(api.calls.some((call) => call.url.includes("/sound-assets/rain/layer-0.ogg"))).toBe(true),
    );
    expect(screen.getByRole("dialog", { name: "Sound" })).toBeInTheDocument();
    expect(soundState()).toMatchObject({ playing: true, started: true, soundscape: "rain", problem: null });
    expect(contexts[0]?.close).toHaveBeenCalledOnce();
    expect(contexts).toHaveLength(2);
    expect(contexts[1]?.sources).toHaveLength(1);
    const choices = await screen.findByRole("dialog", { name: "Sound" });
    await waitFor(() =>
      expect(within(choices).getByRole("switch", { name: "Follow my theme" })).toHaveAttribute("aria-checked", "false"),
    );
    expect(within(choices).getByRole("radio", { name: /^Rain/ })).toHaveAttribute("aria-checked", "true");
    expect(within(choices).getByRole("button", { name: "Pause Rain" })).toBeInTheDocument();
    expect(snowSpeaker).toBeInTheDocument();
  });

  it.each(["fixture-radio", "rain"])(
    "manual choice %s takes over from the theme, including reselecting its sound",
    async (name) => {
      vi.spyOn(page, "reload").mockImplementation(() => undefined);
      sessionStorage.clear();
      let manual = false;
      const { calls } = mockApi({
        ...shell,
        get "/api/system/sound/list"() {
          return soundList(manual ? name : "fixture-radio", {
            follow_theme: !manual,
            effective: manual ? name : "rain",
          });
        },
        get "/api/system/sound/use"() {
          manual = true;
          return soundList(name);
        },
      });
      serveLayers();
      vi.stubGlobal("AudioContext", fakeAudio().Context);
      const user = userEvent.setup();
      renderAt("/");
      await user.click(await speaker(/^Choose a soundscape$/));
      const dialog = await screen.findByRole("dialog", { name: "Sound" });
      expect(within(dialog).getByRole("radio", { name: /^Rain/ })).toHaveAttribute("aria-checked", "true");
      await user.click(
        within(dialog).getByRole("radio", { name: name === "fixture-radio" ? /^Fixture radio/ : /^Rain/ }),
      );
      await waitFor(() => expect(calls.some((call) => call.url.includes(`/system/sound/use?name=${name}`))).toBe(true));
      // The picker stays open. Rain plays at once; the stream's host needs another page first, which plays it after.
      expect(screen.getByRole("dialog", { name: "Sound" })).toBeInTheDocument();
      if (name === "rain") expect(soundState()).toMatchObject({ playing: true, soundscape: "rain" });
      else {
        await waitFor(() => expect(page.reload).toHaveBeenCalled());
        expect(pendingResume()).toBe("fixture-radio");
        expect(soundState().playing).toBe(false);
      }
      await waitFor(() =>
        expect(within(dialog).getByRole("switch", { name: "Follow my theme" })).toHaveAttribute(
          "aria-checked",
          "false",
        ),
      );
      expect(
        within(dialog).getByRole("radio", { name: name === "fixture-radio" ? /^Fixture radio/ : /^Rain/ }),
      ).toHaveAttribute("aria-checked", "true");
    },
  );

  it("shows the owning theme or built-in provenance beside soundscape details", async () => {
    mockApi({
      ...shell,
      "/api/system/sound/list": {
        ...soundList("off"),
        soundscapes: [off, fm, { ...rain, plugin: "heinzel-acme", pairs_with: ["acme"] }],
      },
      "/api/system/theme/list": themeList("acme", [acmeTheme]),
    });
    const user = userEvent.setup();
    renderAt("/");
    const dialog = await openSoundChoices(user);
    expect(await within(dialog).findByText("Theme: Acme · Sounds by Jo Example")).toBeInTheDocument();
    expect(within(dialog).getByText("Built-in · Plays from YouTube")).toBeInTheDocument();
  });

  it("does not attribute an unpaired soundscape to another theme from the same plugin", async () => {
    mockApi({
      ...shell,
      "/api/system/sound/list": {
        ...soundList("off"),
        soundscapes: [off, { ...rain, plugin: "heinzel-acme", pairs_with: ["another"] }],
      },
      "/api/system/theme/list": themeList("acme", [acmeTheme]),
    });
    const user = userEvent.setup();
    renderAt("/");
    const dialog = await openSoundChoices(user);
    expect(await within(dialog).findByText("Plugin: heinzel-acme · Sounds by Jo Example")).toBeInTheDocument();
    expect(within(dialog).queryByText(/Theme: Acme/)).not.toBeInTheDocument();
  });

  it("loads nothing of the stream's host until Play, then its player with the frame attributes it needs", async () => {
    const { calls } = soundApi("fixture-radio");
    const user = userEvent.setup();
    const { container } = renderAt("/");
    await speaker(/^Choose a soundscape$/);
    expect(document.querySelector("iframe")).toBeNull();
    expect(calls.some((call) => !call.url.startsWith(window.location.origin))).toBe(false);
    await expectAccessible(container); // before Play: axe can't look into a cross-origin frame in jsdom
    await playSound(user, "Fixture radio");
    const frame = (await screen.findByTitle("Fixture radio player")) as HTMLIFrameElement;
    expect(new URL(frame.src).origin).toBe(ORIGIN);
    expect(new URL(frame.src).searchParams.get("origin")).toBe(window.location.origin);
    expect(frame.getAttribute("referrerpolicy")).toBe("strict-origin-when-cross-origin");
    expect(frame.getAttribute("sandbox")).toBe(
      "allow-scripts allow-same-origin allow-presentation allow-popups allow-popups-to-escape-sandbox",
    );
    expect(frame.getAttribute("allow")).toBe("autoplay; encrypted-media");
    expect(frame.getAttribute("loading")).toBeNull();
    expect(screen.getByText("Plays from YouTube")).toBeInTheDocument();
    const activeDialog = await screen.findByRole("dialog", { name: "Sound" });
    expect(within(activeDialog).getByRole("button", { name: "Pause Fixture radio" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Stop and close the player" }));
    expect(document.querySelector("iframe")).toBeNull();
  });

  it("does not load YouTube before Play even when this browser remembers account playback", async () => {
    rememberYouTubeAccount(true);
    const { calls } = soundApi("fixture-radio");
    const user = userEvent.setup();
    renderAt("/");
    await speaker(/^Choose a soundscape$/);
    expect(document.querySelector("iframe")).toBeNull();
    expect(calls.some((call) => !call.url.startsWith(window.location.origin))).toBe(false);
    await playSound(user, "Fixture radio");
    const frame = (await screen.findByTitle("Fixture radio player")) as HTMLIFrameElement;
    expect(new URL(frame.src).origin).toBe("https://www.youtube.com");
  });

  it("with nothing picked, the speaker opens the choices instead of playing", async () => {
    soundApi("off");
    const user = userEvent.setup();
    renderAt("/");
    const dialog = await openSoundChoices(user);
    const group = within(dialog).getByRole("radiogroup", { name: "Sound" });
    expect(
      within(group)
        .getAllByRole("radio")
        .map((radio) => radio.textContent),
    ).toEqual(["Fixture radioBuilt-in · Plays from YouTube", "RainPlugin: heinzel-rain · Sounds by Jo Example"]);
    expect(within(group).queryByRole("radio", { name: /^Off/ })).not.toBeInTheDocument();
    expect(soundState().playing).toBe(false);
  });

  it("chooses a soundscape without exposing the Off row or Sound on toggle", async () => {
    vi.spyOn(page, "reload").mockImplementation(() => undefined);
    sessionStorage.clear();
    const { calls } = soundApi("off", "fixture-radio");
    const user = userEvent.setup();
    renderAt("/");
    const dialog = await openSoundChoices(user);
    expect(within(dialog).queryByRole("switch", { name: "Sound on" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("radio", { name: /^Off/ })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("radio", { name: /^Fixture radio/ }));
    await waitFor(() =>
      expect(calls.some((call) => call.url.includes("/system/sound/use?name=fixture-radio"))).toBe(true),
    );
    expect(screen.getByRole("dialog", { name: "Sound" })).toBeInTheDocument();
  });

  it("a pick plays at once, in the click, and keeps the picker open and the keyboard focus on the pick", async () => {
    soundApi("off", "rain");
    serveLayers();
    const { Context, contexts } = fakeAudio();
    vi.stubGlobal("AudioContext", Context);
    const user = userEvent.setup();
    renderAt("/");
    const dialog = await openSoundChoices(user);
    const rainChoice = within(dialog).getByRole("radio", { name: /^Rain/ });
    await user.click(rainChoice);
    expect(soundState()).toMatchObject({ playing: true, started: true, soundscape: "rain" }); // before the answer
    expect(screen.getByRole("dialog", { name: "Sound" })).toBeInTheDocument();
    expect(rainChoice).toHaveFocus();
    expect(within(dialog).getByRole("button", { name: "Pause Rain" })).toBeInTheDocument();
    await waitFor(() => expect(contexts).toHaveLength(1));
    expect(speakerIcon()).toBe("volume-2");
    await user.click(within(dialog).getByRole("button", { name: "Pause Rain" }));
    expect(soundState().playing).toBe(false);
    expect(within(dialog).getByRole("button", { name: "Play Rain" })).toBeInTheDocument();
    expect(speakerIcon()).toBe("volume-x");
    await user.click(rainChoice); // the pick that is already chosen plays it again
    expect(soundState()).toMatchObject({ playing: true, soundscape: "rain" });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Sound" })).toBeNull();
    expect(await speaker(/^Choose a soundscape$/)).toHaveFocus();
  });

  it("a click outside closes the picker and leaves the sound playing", async () => {
    soundApi("off", "rain");
    serveLayers();
    vi.stubGlobal("AudioContext", fakeAudio().Context);
    const user = userEvent.setup();
    renderAt("/");
    const dialog = await openSoundChoices(user);
    await user.click(within(dialog).getByRole("radio", { name: /^Rain/ }));
    await user.click(document.body);
    expect(screen.queryByRole("dialog", { name: "Sound" })).toBeNull();
    expect(soundState().playing).toBe(true);
  });

  it("after a pick reloaded the page for a stream's host, the page plays it and opens the picker again", async () => {
    sessionStorage.setItem("hz.sound.resume", JSON.stringify({ name: "fixture-radio", at: Date.now() }));
    soundApi("fixture-radio");
    renderAt("/");
    expect(await screen.findByRole("dialog", { name: "Sound" })).toBeInTheDocument();
    await waitFor(() => expect(soundState()).toMatchObject({ playing: true, soundscape: "fixture-radio" }));
    expect(await screen.findByTitle("Fixture radio player")).toBeInTheDocument();
    expect(pendingResume()).toBeNull(); // once
  });

  it("ignores a stale or foreign resume note", async () => {
    sessionStorage.setItem("hz.sound.resume", JSON.stringify({ name: "fixture-radio", at: Date.now() - 60_000 }));
    soundApi("fixture-radio");
    renderAt("/");
    await speaker(/^Choose a soundscape$/);
    expect(screen.queryByRole("dialog", { name: "Sound" })).toBeNull();
    expect(soundState().playing).toBe(false);
  });

  it("the visible speaker opens sound choices from the keyboard and restores focus on Escape", async () => {
    soundApi("fixture-radio");
    const user = userEvent.setup();
    renderAt("/");
    const speakerButton = (await speaker(/^Choose a soundscape$/)) as HTMLElement;
    speakerButton.focus();
    await act(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())));
    expect(speakerButton).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    const dialog = await screen.findByRole("dialog", { name: "Sound" });
    expect(speakerButton).toHaveAttribute("aria-expanded", "true");
    expect(within(dialog).getByRole("slider", { name: "Volume" })).toHaveValue("50");
    expect(within(dialog).getByRole("switch", { name: "Follow my theme" })).toHaveAttribute("aria-checked", "false");
    expect(within(dialog).getByRole("switch", { name: "Mute during meetings" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Sound" })).toBeNull();
    expect(speakerButton).toHaveFocus();
  });

  it("a right-click on the speaker opens the popover too", async () => {
    soundApi("fixture-radio");
    const user = userEvent.setup();
    renderAt("/");
    await user.click(await speaker(/^Choose a soundscape$/));
    expect(await screen.findByRole("dialog", { name: "Sound" })).toBeInTheDocument();
    expect(soundState().playing).toBe(false);
  });

  it("Settings → General has the full picker with every option", async () => {
    soundApi("off");
    renderAt("/?settings=hz");
    const section = await screen.findByRole("region", { name: "Sound" });
    expect(within(section).getAllByRole("radio")).toHaveLength(2);
    expect(within(section).getByRole("switch", { name: "Softer while I type" })).toBeInTheDocument();
  });

  it("typing in the chat ducks a playing soundscape", async () => {
    soundApi("fixture-radio");
    const user = userEvent.setup();
    renderAt("/");
    await playSound(user, "Fixture radio");
    const input = await screen.findByRole("textbox", { name: /message/i });
    await user.type(input, "hi");
    expect(soundState().typing).toBe(true);
  });
});

describe("the page's frame origin", () => {
  it("needs a reload only when the soundscape in effect needs another frame origin than the page admits", () => {
    rememberPageFrame(soundList("off"));
    expect(needsReload(soundList("rain"))).toBe(false);
    expect(needsReload(soundList("fixture-radio"))).toBe(true);
    resetPageFrame();
    rememberPageFrame(soundList("fixture-radio"));
    rememberPageFrame(soundList("off")); // the first list decides: it is what the server served the page with
    expect(needsReload(soundList("fixture-radio"))).toBe(false);
    expect(needsReload(soundList("off"))).toBe(true);
  });

  it("picking a stream while the page admits none reloads the page", async () => {
    const reload = vi.spyOn(page, "reload").mockImplementation(() => undefined);
    soundApi("off", "fixture-radio");
    const user = userEvent.setup();
    renderAt("/");
    const dialog = await openSoundChoices(user);
    await act(async () => {
      await user.click(within(dialog).getByRole("radio", { name: /^Fixture radio/ }));
    });
    await waitFor(() => expect(reload).toHaveBeenCalled());
  });
});

describe("a layered soundscape", () => {
  /** The API, plus the audio files hz serves; `fetch` answers each with its path, which the fake decoder reads. */
  function layeredApi() {
    const api = soundApi("rain");
    const answer = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request | URL) => {
        const url = input instanceof URL ? input : new URL(input.url);
        if (url.pathname.startsWith("/sound-assets/")) {
          api.calls.push(new Request(url));
          return new Response(url.pathname);
        }
        return answer(input as Request);
      }),
    );
    return api;
  }

  it("fetches nothing before Play, then mixes its layers and fades them in", async () => {
    const { Context, contexts } = fakeAudio();
    vi.stubGlobal("AudioContext", Context);
    const { calls } = layeredApi();
    const user = userEvent.setup();
    renderAt("/");
    await speaker(/^Choose a soundscape$/);
    expect(calls.some((call) => call.url.includes("/sound-assets/"))).toBe(false);
    expect(contexts).toHaveLength(0);
    await playSound(user, "Rain");
    await waitFor(() => expect(calls.some((call) => call.url.includes("/sound-assets/rain/layer-0.ogg"))).toBe(true));
    const context = contexts.at(-1) as FakeContext;
    await waitFor(() => expect(context.gains[0]?.gain.target).toBeCloseTo(0.5)); // the master, to the volume
    expect(context.sources[0]?.loop).toBe(true);
    await user.click(
      within(await screen.findByRole("dialog", { name: "Sound" })).getByRole("button", { name: "Pause Rain" }),
    );
    await waitFor(() => expect(context.gains[0]?.gain.target).toBe(0));
    await act(async () => {
      window.dispatchEvent(new CustomEvent("hz:notice", { detail: { kind: "draft" } }));
    });
    expect(context.sources).toHaveLength(1); // paused: no earcon (and this soundscape has none)
  });

  it("says so when the browser can't play layered sound, and Play can be pressed again", async () => {
    vi.stubGlobal("AudioContext", undefined);
    layeredApi();
    const user = userEvent.setup();
    renderAt("/");
    await playSound(user, "Rain");
    await waitFor(() => expect(soundState().problem).toBe("This browser can't play layered sound."));
    expect(soundState()).toMatchObject({ playing: false, started: false });
  });

  it("says so when a layer can't be loaded, and stopping while it loads is no failure", async () => {
    const { Context, contexts } = fakeAudio(() => 1000);
    vi.stubGlobal("AudioContext", Context);
    layeredApi();
    const user = userEvent.setup();
    renderAt("/");
    await playSound(user, "Rain");
    await waitFor(() => expect(soundState().problem).toBe("Rain couldn't be loaded."));
    await waitFor(() => expect(contexts.at(-1)?.close).toHaveBeenCalled());
  });
});

describe("a stream's fades", () => {
  it("leaves iPhone volume to the device even when saved volume is zero, and mutes while recording voice", async () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (iPhone) AppleWebKit/605.1.15");
    mockApi({ ...shell, "/api/system/sound/list": soundList("fixture-radio", { volume: 0 }) });
    const user = userEvent.setup();
    renderAt("/");
    await playSound(user, "Fixture radio");
    const frame = (await screen.findByTitle("Fixture radio player")) as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow as Window, "postMessage");
    const sent = () => post.mock.calls.map(([data]) => JSON.parse(String(data)).func);
    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", { data: { event: "onReady" }, origin: ORIGIN, source: frame.contentWindow }),
      );
    });
    expect(sent()).toEqual(["mute", "unMute", "playVideo"]);
    post.mockClear();
    act(() => duck("voice", true));
    expect(sent()).toEqual(["mute", "playVideo"]);
    post.mockClear();
    act(() => duck("voice", false));
    expect(sent()).toEqual(["unMute", "playVideo"]);
  });

  it("is silent when the player is ready, plays, then steps its volume up, and pauses after fading out", async () => {
    soundApi("fixture-radio");
    const user = userEvent.setup();
    renderAt("/");
    await playSound(user, "Fixture radio");
    const frame = (await screen.findByTitle("Fixture radio player")) as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow as Window, "postMessage");
    const sent = () => post.mock.calls.map(([data, target]) => [JSON.parse(String(data)).func, target]);
    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", { data: '{"event":"onReady"}', origin: "https://evil.example" }),
      );
    });
    expect(post).not.toHaveBeenCalled(); // another origin is not our player
    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", { data: '{"event":"onReady"}', origin: ORIGIN, source: frame.contentWindow }),
      );
    });
    expect(sent()[0]).toEqual(["setVolume", ORIGIN]);
    expect(JSON.parse(String(post.mock.calls[0]?.[0])).args).toEqual([0]);
    expect(sent()[1]).toEqual(["playVideo", ORIGIN]);
    await waitFor(() => expect(JSON.parse(String(post.mock.calls.at(-1)?.[0])).args).toEqual([50]), { timeout: 6000 });
    await user.click(
      within(await screen.findByRole("dialog", { name: "Sound" })).getByRole("button", { name: "Pause Fixture radio" }),
    );
    await waitFor(() => expect(sent().at(-1)).toEqual(["pauseVideo", ORIGIN]), { timeout: 6000 });
    expect(post.mock.calls.every((call) => (call as unknown[])[1] === ORIGIN)).toBe(true);
  }, 20_000);

  it("offers a reload when the page's CSP blocks the player", async () => {
    soundApi("fixture-radio");
    const user = userEvent.setup();
    renderAt("/");
    await playSound(user, "Fixture radio");
    await screen.findByTitle("Fixture radio player");
    const reload = vi.spyOn(page, "reload").mockImplementation(() => undefined);
    await act(async () => {
      const event = new Event("securitypolicyviolation") as SecurityPolicyViolationEvent;
      Object.assign(event, { effectiveDirective: "frame-src", blockedURI: `${ORIGIN}/embed/abcdefghijk` });
      document.dispatchEvent(event);
    });
    expect(screen.queryByTitle("Fixture radio player")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalled();
  });
});
