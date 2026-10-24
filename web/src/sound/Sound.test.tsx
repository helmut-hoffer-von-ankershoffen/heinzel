import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SchemaSoundList, SchemaSoundscapeView } from "@/api/client";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { needsReload, page, rememberPageFrame, resetPageFrame } from "./queries";
import { resetSound, soundState } from "./store";

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
  name: "claude-fm",
  title: "Claude FM",
  kind: "stream",
  module: "system.sound",
  plugin: "heinzel",
  selected: false,
  effective: false,
  fade_seconds: 4,
  stream: {
    provider: "youtube",
    video: "tRsQsTMvPNg",
    embed_url: `${ORIGIN}/embed/tRsQsTMvPNg`,
    frame_origin: ORIGIN,
    watch_url: "https://www.youtube.com/watch?v=tRsQsTMvPNg",
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

afterEach(() => {
  resetSound();
  resetPageFrame();
  vi.restoreAllMocks();
});

const speaker = (name: RegExp) => screen.findByRole("button", { name });

describe("Sound", () => {
  it("loads nothing of the stream's host until Play, then its player with the frame attributes it needs", async () => {
    const { calls } = soundApi("claude-fm");
    const user = userEvent.setup();
    const { container } = renderAt("/");
    const button = await speaker(/^Play Claude FM$/);
    expect(button).toHaveAttribute("aria-pressed", "false");
    expect(document.querySelector("iframe")).toBeNull();
    expect(calls.some((call) => !call.url.startsWith(window.location.origin))).toBe(false);
    await expectAccessible(container); // before Play: axe can't look into a cross-origin frame in jsdom
    await user.click(button);
    const frame = (await screen.findByTitle("Claude FM player")) as HTMLIFrameElement;
    expect(new URL(frame.src).origin).toBe(ORIGIN);
    expect(new URL(frame.src).searchParams.get("origin")).toBe(window.location.origin);
    expect(frame.getAttribute("referrerpolicy")).toBe("strict-origin-when-cross-origin");
    expect(frame.getAttribute("sandbox")).toBe(
      "allow-scripts allow-same-origin allow-presentation allow-popups allow-popups-to-escape-sandbox",
    );
    expect(frame.getAttribute("allow")).toBe("autoplay; encrypted-media");
    expect(frame.getAttribute("loading")).toBeNull();
    expect(screen.getByText("Plays from YouTube")).toBeInTheDocument();
    expect(await speaker(/^Pause Claude FM$/)).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Stop and close the player" }));
    expect(document.querySelector("iframe")).toBeNull();
  });

  it("with nothing picked, the speaker opens the choices instead of playing", async () => {
    soundApi("off");
    const user = userEvent.setup();
    renderAt("/");
    await user.click(await speaker(/^Choose a soundscape$/));
    const dialog = await screen.findByRole("dialog", { name: "Sound" });
    const group = within(dialog).getByRole("radiogroup", { name: "Sound" });
    expect(
      within(group)
        .getAllByRole("radio")
        .map((radio) => radio.textContent),
    ).toEqual(["Off", "Claude FMPlays from YouTube", "RainSounds by Jo Example"]);
    expect(within(group).getByRole("radio", { name: /^Off/ })).toHaveAttribute("aria-checked", "true");
    expect(soundState().playing).toBe(false);
  });

  it("the chevron opens the popover from the keyboard, Escape closes it and gives focus back", async () => {
    const { calls } = soundApi("claude-fm");
    const user = userEvent.setup();
    renderAt("/");
    const chevron = (await screen.findAllByRole("button", { name: "Sound options" }))[0] as HTMLElement;
    chevron.focus();
    await user.keyboard("{Enter}");
    const dialog = await screen.findByRole("dialog", { name: "Sound" });
    expect(chevron).toHaveAttribute("aria-expanded", "true");
    expect(within(dialog).getByRole("slider", { name: "Volume" })).toHaveValue("50");
    expect(within(dialog).getByRole("switch", { name: "Follow my theme" })).toHaveAttribute("aria-checked", "false");
    expect(within(dialog).getByRole("switch", { name: "Mute during meetings" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await user.click(within(dialog).getByRole("radio", { name: /^Rain/ }));
    await waitFor(() => expect(calls.some((call) => call.url.includes("/system/sound/use?name=rain"))).toBe(true));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Sound" })).toBeNull();
    expect(chevron).toHaveFocus();
  });

  it("a right-click on the speaker opens the popover too", async () => {
    soundApi("claude-fm");
    const user = userEvent.setup();
    renderAt("/");
    await user.pointer({ keys: "[MouseRight]", target: await speaker(/^Play Claude FM$/) });
    expect(await screen.findByRole("dialog", { name: "Sound" })).toBeInTheDocument();
    expect(soundState().playing).toBe(false);
  });

  it("Settings → General has the full picker with every option", async () => {
    soundApi("off");
    renderAt("/?settings=hz");
    const section = await screen.findByRole("region", { name: "Sound" });
    expect(within(section).getAllByRole("radio")).toHaveLength(3);
    expect(within(section).getByRole("switch", { name: "Softer while I type" })).toBeInTheDocument();
  });

  it("typing in the chat ducks a playing soundscape", async () => {
    soundApi("claude-fm");
    const user = userEvent.setup();
    renderAt("/");
    await user.click(await speaker(/^Play Claude FM$/));
    const input = await screen.findByRole("textbox", { name: /message/i });
    await user.type(input, "hi");
    expect(soundState().typing).toBe(true);
  });
});

describe("the page's frame origin", () => {
  it("needs a reload only when the soundscape in effect needs another frame origin than the page admits", () => {
    rememberPageFrame(soundList("off"));
    expect(needsReload(soundList("rain"))).toBe(false);
    expect(needsReload(soundList("claude-fm"))).toBe(true);
    resetPageFrame();
    rememberPageFrame(soundList("claude-fm"));
    rememberPageFrame(soundList("off")); // the first list decides: it is what the server served the page with
    expect(needsReload(soundList("claude-fm"))).toBe(false);
    expect(needsReload(soundList("off"))).toBe(true);
  });

  it("picking a stream while the page admits none reloads the page", async () => {
    const reload = vi.spyOn(page, "reload").mockImplementation(() => undefined);
    soundApi("off", "claude-fm");
    const user = userEvent.setup();
    renderAt("/");
    await user.click(await speaker(/^Choose a soundscape$/));
    const dialog = await screen.findByRole("dialog", { name: "Sound" });
    await act(async () => {
      await user.click(within(dialog).getByRole("radio", { name: /^Claude FM/ }));
    });
    await waitFor(() => expect(reload).toHaveBeenCalled());
  });
});
