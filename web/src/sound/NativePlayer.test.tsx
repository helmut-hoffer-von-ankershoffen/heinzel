import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SchemaSoundList, SchemaSoundscapeView } from "@/api/client";
import { actionStructureProblems } from "@/components/PaneActions";
import { fakeMedia } from "@/test/fakeMedia";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi } from "@/test/render";
import { renderAt } from "@/test/renderApp";
import { clock } from "./NativePlayer";
import { needsReload, page, rememberPagePolicy, resetPagePolicy, SOUND_KEY } from "./queries";
import { duck, resetSound, soundState } from "./store";

const base = { module: "sound-demo", plugin: "heinzel-sound-demo", selected: false, effective: false, fade_seconds: 1 };
const album: SchemaSoundscapeView = {
  ...base,
  name: "album",
  title: "Fixture album",
  kind: "stream",
  stream: null,
  tracks: ["First tone", "Second tone"].map((title, index) => ({
    title,
    url: `https://media.heinzel.team/fixture/${index}.mp3`,
    type: "audio/mpeg",
    duration_seconds: 187,
  })),
};
const radio: SchemaSoundscapeView = {
  ...base,
  name: "radio",
  title: "Fixture radio",
  kind: "stream",
  stream: {
    provider: "youtube",
    video: "abcdefghijk",
    embed_url: "https://www.youtube-nocookie.com/embed/abcdefghijk",
    frame_origin: "https://www.youtube-nocookie.com",
    watch_url: "https://www.youtube.com/watch?v=abcdefghijk",
  },
};
const both: SchemaSoundscapeView = { ...album, name: "both", title: "Fixture both", stream: radio.stream };

function soundList(selected: string): SchemaSoundList {
  return {
    selected,
    effective: selected,
    soundscapes: [album, radio, both].map((s) => ({
      ...s,
      selected: s.name === selected,
      effective: s.name === selected,
    })),
    signals: {},
    volume: 50,
    follow_theme: false,
    mute_in_meetings: false,
    duck_while_typing: true,
  };
}

let served: SchemaSoundList;
let refresh: () => Promise<void>;

async function open(selected: string) {
  served = soundList(selected);
  mockApi({
    ...shell,
    get "/api/system/sound/list"() {
      return served;
    },
    "/api/system/sound/use": soundList(selected),
  });
  const user = userEvent.setup();
  const { client } = renderAt("/");
  refresh = () => client.refetchQueries({ queryKey: SOUND_KEY });
  await user.click(await screen.findByRole("button", { name: "Choose a soundscape" }));
  const dialog = await screen.findByRole("dialog", { name: "Sound" });
  return { user, dialog };
}

async function play(selected: string, title: string) {
  const { user, dialog } = await open(selected);
  await user.click(within(dialog).getByRole("button", { name: `Play ${title}` }));
  const player = await screen.findByRole("region", { name: `${title} player` });
  return { user, player };
}

let media: ReturnType<typeof fakeMedia>;
beforeEach(() => {
  media = fakeMedia();
});
afterEach(() => {
  resetSound();
  resetPagePolicy();
  media.restore();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("native tracks", () => {
  it.each(["album", "both"])("put no element or frame in the page before Play (%s)", async (name) => {
    const { dialog } = await open(name);
    expect(dialog).toHaveTextContent("Plays from media.heinzel.team · 2 tracks, 6 min");
    expect(document.querySelector("audio, iframe")).toBeNull();
    expect(media.play).not.toHaveBeenCalled();
  });

  it("play in an audio element, in order, in the shared card", async () => {
    const { player } = await play("album", "Fixture album");
    const audio = player.querySelector("audio") as HTMLAudioElement;
    expect(media.play).toHaveBeenCalled();
    expect(audio.src).toBe(album.tracks?.[0]?.url);
    expect(player).toHaveTextContent("Plays from media.heinzel.team");
    expect(player).toHaveTextContent("First tone");
    expect(player).toHaveTextContent("Track 1 of 2 · 3:07");
    expect(within(player).queryByRole("link")).toBeNull();
    act(() => media.end(audio));
    await waitFor(() => expect(player).toHaveTextContent("Second tone"));
    expect(audio.paused).toBe(false);
  });

  it("keep the card accessible and in the action structure", async () => {
    const { player } = await play("both", "Fixture both");
    expect(actionStructureProblems(player)).toEqual([]);
    await expectAccessible(player);
  });

  it("follow Next, Previous and Pause of the card", async () => {
    const { user, player } = await play("album", "Fixture album");
    await user.click(within(player).getByRole("button", { name: "Next track" }));
    expect(player).toHaveTextContent("Second tone");
    await user.click(within(player).getByRole("button", { name: "Previous track" }));
    expect(player).toHaveTextContent("First tone");
    await user.click(within(player).getByRole("button", { name: "Pause" }));
    expect(soundState().playing).toBe(false);
    expect(await within(player).findByRole("button", { name: "Play" })).toBeVisible();
  });

  it("let the lock screen pause and play what the app shows", async () => {
    await play("album", "Fixture album");
    act(() => {
      media.press("pause");
    });
    expect(soundState().playing).toBe(false);
    act(() => {
      media.press("play");
    });
    expect(soundState().playing).toBe(true);
  });

  it("contact YouTube only when the person picks it, and then let go of the tracks", async () => {
    const { user, player } = await play("both", "Fixture both");
    const audio = player.querySelector("audio") as HTMLAudioElement;
    expect(document.querySelector("iframe")).toBeNull();
    expect(within(player).getByRole("link", { name: "Open on YouTube" })).toHaveAttribute(
      "href",
      both.stream?.watch_url,
    );
    await user.click(within(player).getByRole("button", { name: "Play from YouTube instead" }));
    await waitFor(() => expect(document.querySelector("iframe")).not.toBeNull());
    expect(document.querySelector("audio")).toBeNull();
    expect(audio.paused).toBe(true);
    expect(audio.hasAttribute("src")).toBe(false);
  });

  it("offer no YouTube link in the desktop app, which can't use it", async () => {
    vi.stubGlobal("heinzelDesktop", { lifecycle: () => undefined });
    const { player } = await play("both", "Fixture both");
    expect(within(player).queryByRole("link")).toBeNull();
  });

  it("follow an iPhone that resumes after a call, so voice input still mutes", async () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (iPhone) AppleWebKit/605.1.15");
    const { player } = await play("album", "Fixture album");
    const audio = player.querySelector("audio") as HTMLAudioElement;
    act(() => media.interrupt(audio));
    expect(soundState().playing).toBe(false);
    act(() => media.resume(audio));
    expect(soundState().playing).toBe(true);
    act(() => duck("voice", true));
    expect(audio.muted).toBe(true);
  });

  it("offer Try again when a track doesn't load", async () => {
    const { user, player } = await play("album", "Fixture album");
    const audio = player.querySelector("audio") as HTMLAudioElement;
    act(() => media.fail(audio));
    expect(await within(player).findByRole("alert")).toHaveTextContent("This track didn't load.");
    expect(actionStructureProblems(player)).toEqual([]);
    await user.click(within(player).getByRole("button", { name: "Try again" }));
    expect(within(player).queryByRole("alert")).toBeNull();
    expect(audio.paused).toBe(false);
  });

  it("offer Reload when the page's policy predates the pick", async () => {
    const reload = vi.spyOn(page, "reload").mockImplementation(() => undefined);
    const { user, player } = await play("album", "Fixture album");
    act(() => {
      const event = new Event("securitypolicyviolation");
      Object.assign(event, { effectiveDirective: "media-src", blockedURI: "https://media.heinzel.team" });
      document.dispatchEvent(event);
    });
    await user.click(await within(player).findByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalledOnce();
  });

  it("leave the volume to an iPhone, and mute while voice input records", async () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (iPhone) AppleWebKit/605.1.15");
    const { user, dialog } = await open("album");
    expect(within(dialog).queryByRole("slider")).toBeNull();
    expect(within(dialog).queryByRole("switch", { name: "Softer while I type" })).toBeNull();
    expect(dialog).toHaveTextContent("Use your iPhone or iPad volume buttons for this music.");
    await user.click(within(dialog).getByRole("button", { name: "Play Fixture album" }));
    const audio = document.querySelector("audio") as HTMLAudioElement;
    expect(audio.muted).toBe(false);
    act(() => duck("voice", true));
    expect(audio.muted).toBe(true);
    act(() => duck("voice", false));
    expect(audio.muted).toBe(false);
  });
});

describe("a list that changes under the player", () => {
  const replace = (changed: SchemaSoundscapeView): SchemaSoundList => ({
    ...served,
    soundscapes: served.soundscapes.map((item) =>
      item.name === changed.name ? { ...changed, selected: true, effective: true } : item,
    ),
  });

  it("never turns tracks into a YouTube frame nobody picked", async () => {
    await play("both", "Fixture both");
    served = replace({ ...both, tracks: [] }); // the feature was switched off
    await act(() => refresh());
    await waitFor(() => expect(soundState().started).toBe(false));
    expect(document.querySelector("iframe, audio")).toBeNull();
  });

  it("never turns picked YouTube back into tracks", async () => {
    const { user, player } = await play("both", "Fixture both");
    await user.click(within(player).getByRole("button", { name: "Play from YouTube instead" }));
    media.play.mockClear();
    const [first] = both.tracks ?? [];
    served = replace({ ...both, tracks: first ? [{ ...first, url: "https://media.heinzel.team/new.mp3" }] : [] });
    await act(() => refresh());
    await waitFor(() => expect(soundState().started).toBe(false));
    expect(media.play).not.toHaveBeenCalled();
    expect(document.querySelector("audio")).toBeNull();
  });

  it("never plays tracks that come into effect from elsewhere", async () => {
    await play("radio", "Fixture radio");
    media.play.mockClear();
    served = soundList("album"); // picked in a terminal
    await act(() => refresh());
    await waitFor(() => expect(soundState().started).toBe(false));
    expect(media.play).not.toHaveBeenCalled();
    expect(document.querySelector("audio")).toBeNull();
  });
});

describe("the page's policy", () => {
  it.each([
    ["off", "album", true],
    ["album", "off", true],
    ["radio", "both", true],
    ["both", "album", true],
    ["album", "album", false],
    ["radio", "radio", false],
  ])("from %s to %s needs a reload: %s", (before, after, reload) => {
    rememberPagePolicy(soundList(before));
    expect(needsReload(soundList(after))).toBe(reload);
  });
});

it.each([
  [61, "1:01"],
  [3600, "1:00:00"],
  [3723, "1:02:03"],
])("writes %i seconds as %s", (seconds, words) => {
  expect(clock(seconds)).toBe(words);
});
