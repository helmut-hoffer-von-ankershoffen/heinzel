import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { shell } from "@/test/fixtures";
import { mockApi } from "@/test/render";
import { renderAt } from "@/test/renderApp";
import { ALBUM_TITLE, type AlbumAction, type AlbumSnapshot, nativeAlbum } from "./album";
import { resetDock, setDocked } from "./dock";
import { resetSound, resume, soundState } from "./store";

const downloading: AlbumSnapshot = {
  phase: "downloading",
  verified: 2,
  total: 37,
  verifiedBytes: 100,
  receivedBytes: 200,
  totalBytes: 800,
  error: null,
  title: "Berlin Morning",
  playing: true,
  repeatMode: "Album",
  positionSeconds: 18,
  durationSeconds: 180,
  media: {
    albumID: "electric",
    selectedTrackID: "berlin-morning",
    tracks: [
      { id: "berlin-morning", title: "Berlin Morning" },
      { id: "berlin-evening", title: "Berlin Evening" },
    ],
    extendedVideo: null,
  },
};
afterEach(() => {
  resetSound();
  resetDock();
  vi.unstubAllGlobals();
});
async function open() {
  mockApi({
    ...shell,
    "/api/system/sound/list": {
      selected: "singularity",
      effective: "singularity",
      volume: 50,
      follow_theme: false,
      mute_in_meetings: false,
      duck_while_typing: false,
      signals: {},
      soundscapes: [
        {
          name: "singularity",
          title: "Singularity",
          kind: "stream",
          plugin: "heinzel",
          module: "system.sound",
          selected: true,
          effective: true,
          fade_seconds: 1,
          stream: {
            provider: "youtube",
            video: "abcdefghijk",
            embed_url: "https://www.youtube-nocookie.com/embed/abcdefghijk",
            frame_origin: "https://www.youtube-nocookie.com",
            watch_url: "https://www.youtube.com/watch?v=abcdefghijk",
          },
        },
      ],
    },
  });
  const user = userEvent.setup();
  const view = renderAt("/");
  await user.click(await screen.findByRole("button", { name: /^Choose a soundscape/ }));
  return { user, view, dialog: screen.getByRole(nativeAlbum() ? "region" : "dialog", { name: "Sound" }) };
}
it("uses native album state and transport in the collapsed rail without starting ordinary audio", async () => {
  let status = downloading;
  const album = vi.fn(async (action: AlbumAction) => {
    if (action === "toggle") status = { ...status, playing: !status.playing };
    return status;
  });
  vi.stubGlobal("heinzelDesktop", { album });
  const { user, dialog } = await open();
  await user.click(within(dialog).getByRole("radio", { name: /Dreaming of Electric Snow/ }));
  const dock = await screen.findByRole("region", { name: `${ALBUM_TITLE} player` });
  await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
  expect(dock).not.toBeVisible();
  await user.click(await screen.findByRole("button", { name: `Pause ${ALBUM_TITLE}` }));
  expect(await screen.findByRole("button", { name: `Play ${ALBUM_TITLE}` })).toBeInTheDocument();
  expect(status.playing).toBe(false);
  expect(soundState()).toMatchObject({ albumSelected: true, playing: false });
  await user.click(screen.getByRole("button", { name: `Play ${ALBUM_TITLE}` }));
  expect(await screen.findByRole("button", { name: `Pause ${ALBUM_TITLE}` })).toBeInTheDocument();
  expect(status.playing).toBe(true);
  expect(soundState()).toMatchObject({ albumSelected: true, playing: false });
  expect(album.mock.calls.filter(([action]) => action === "toggle")).toHaveLength(2);
  expect(album).not.toHaveBeenCalledWith("pausePlayback");
  expect(document.querySelector("iframe")).toBeNull();
  status = { ...status, playing: false };
  act(() => window.dispatchEvent(new Event("heinzel-album-change")));
  expect(await screen.findByRole("button", { name: `Play ${ALBUM_TITLE}` })).toBeInTheDocument();
});

it("returns a popped-out album to the dock on explicit album selection", async () => {
  const album = vi.fn(async (_action: AlbumAction) => downloading);
  vi.stubGlobal("heinzelDesktop", { album });
  const { user } = await open();
  const dock = await screen.findByRole("region", { name: `${ALBUM_TITLE} player` });
  await user.keyboard("{Escape}");
  await user.click(within(dock).getByRole("button", { name: "Pop the player out of the sidebar" }));
  expect(dock).toHaveAttribute("data-dock", "floating");
  expect(JSON.parse(localStorage.getItem("hz.sound.player") ?? "null")).toMatchObject({ docked: false });
  await user.click(screen.getByRole("button", { name: /^Choose a soundscape/ }));
  await user.click(
    within(screen.getByRole(nativeAlbum() ? "region" : "dialog", { name: "Sound" })).getByRole("radio", {
      name: /Dreaming of Electric Snow/,
    }),
  );
  expect(dock).toHaveAttribute("data-dock", "docked");
  expect(JSON.parse(localStorage.getItem("hz.sound.player") ?? "null")).toMatchObject({ docked: true });
  expect(screen.queryByRole("dialog", { name: "Sound" })).toBeNull();
  expect(album).toHaveBeenCalledWith("play");
});

it("restores native status in the dock and keeps the reopened picker for sources", async () => {
  let status = downloading;
  const album = vi.fn(async (_action: AlbumAction) => status);
  vi.stubGlobal("heinzelDesktop", { album });
  const { user, dialog } = await open();
  await waitFor(() =>
    expect(within(dialog).getByRole("radio", { name: /Dreaming of Electric Snow/ })).toHaveAttribute(
      "aria-checked",
      "true",
    ),
  );
  expect(within(dialog).getByRole("radio", { name: /Singularity/ })).toHaveAttribute("aria-checked", "false");
  expect(within(dialog).queryByRole("progressbar")).toBeNull();
  expect(within(dialog).queryByRole("button", { name: "Pause Album" })).toBeNull();
  const dock = await screen.findByRole("region", { name: `${ALBUM_TITLE} player` });
  expect(dock).toHaveAttribute("data-dock", "docked");
  const credit = within(dock).getByRole("link", { name: "Latent Heinzels" });
  expect(credit).toHaveAttribute("href", "https://latentheinzels.heinzel.team");
  expect(credit).toHaveAttribute("rel", "noopener noreferrer");
  expect(within(dock).getByText("Playing · Berlin Morning")).toBeInTheDocument();
  expect(within(dock).getByRole("combobox", { name: "Choose album track" })).toBeInTheDocument();
  expect(within(dock).getAllByText(ALBUM_TITLE)).toHaveLength(1);
  expect(within(dock).queryByText("200 / 800 bytes")).toBeNull();
  expect(within(dock).getByText("Downloading album · 25%")).toBeInTheDocument();
  expect(within(dock).getByRole("progressbar", { name: "Album download" })).toHaveAttribute("aria-valuenow", "25");
  expect(within(dock).getByRole("progressbar", { name: "Album download" })).toHaveAttribute(
    "aria-valuetext",
    "200 of 800 bytes",
  );
  status = { ...downloading, playing: false, title: "Berlin Evening", repeatMode: "Track", verified: 7 };
  act(() => window.dispatchEvent(new Event("heinzel-album-change")));
  expect(await within(dock).findByText("Paused · Berlin Evening")).toBeInTheDocument();
  expect(within(dock).getByRole("combobox", { name: "Choose album track" })).toBeInTheDocument();
  expect(within(dock).getByText("Downloading album · 25%")).toBeInTheDocument();
  expect(within(dock).getByRole("button", { name: "Repeat: Track" })).toHaveAttribute("aria-pressed", "true");
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("button", { name: /^Choose a soundscape/ }));
  expect(
    within(screen.getByRole(nativeAlbum() ? "region" : "dialog", { name: "Sound" })).getByRole("radio", {
      name: /Singularity/,
    }),
  ).toBeInTheDocument();
  expect(screen.getAllByRole("progressbar", { name: "Album download" })).toHaveLength(1);
  expect(album.mock.calls.every(([action]) => action === "status")).toBe(true);
});
it("connects dock operations and keeps saving independent of playback", async () => {
  let status = downloading;
  const album = vi.fn(async (action: AlbumAction) => {
    if (action === "toggle") status = { ...status, playing: !status.playing };
    if (action === "pauseDownload") status = { ...status, phase: "paused" };
    if (action === "resumeDownload") status = { ...status, phase: "downloading" };
    if (action === "cancelDownload") status = { ...status, phase: "cancelled" };
    return status;
  });
  vi.stubGlobal("heinzelDesktop", { album });
  const { user } = await open();
  const dock = await screen.findByRole("region", { name: `${ALBUM_TITLE} player` });
  for (const [name, action] of [
    ["Previous track", "previous"],
    ["Next track", "next"],
    ["Repeat: Album", "repeat"],
  ]) {
    await user.click(within(dock).getByRole("button", { name }));
    expect(album).toHaveBeenCalledWith(action);
  }
  await user.click(within(dock).getByRole("button", { name: "Pause Album" }));
  expect(await within(dock).findByText(/Downloading album ·/)).toBeInTheDocument();
  await user.click(within(dock).getByRole("button", { name: "Play Album" }));
  await user.click(within(dock).getByRole("button", { name: "Pause download" }));
  expect(await within(dock).findByText("Playing · Berlin Morning")).toBeInTheDocument();
  await user.click(await within(dock).findByRole("button", { name: "Resume download" }));
  expect(album).toHaveBeenCalledWith("resumeDownload");
  await user.click(await within(dock).findByRole("button", { name: "Cancel download (keep verified files)" }));
  expect(album).toHaveBeenCalledWith("cancelDownload");
  expect(await within(dock).findByText("Playing · Berlin Morning")).toBeInTheDocument();
  expect(within(dock).getByText("Album download cancelled")).toBeInTheDocument();
});
it("leaves ordinary Sound controls alone without the capability", async () => {
  const { dialog } = await open();
  expect(within(dialog).getByRole("button", { name: "Play Singularity" })).toBeInTheDocument();
  expect(within(dialog).queryByRole("button", { name: "Open video" })).toBeNull();
});

it("uses play for the first track and waits for the actual native pause before ordinary playback", async () => {
  let status: AlbumSnapshot = { ...downloading, title: "", playing: false, phase: "idle" };
  let finishPause: (() => void) | undefined;
  const album = vi.fn(async (action: AlbumAction) => {
    if (action === "play") status = { ...status, title: "Berlin Morning", playing: true, phase: "downloading" };
    if (action === "pausePlayback") {
      await new Promise<void>((resolve) => {
        finishPause = resolve;
      });
      status = { ...status, playing: false };
    }
    return status;
  });
  vi.stubGlobal("heinzelDesktop", { album });
  const { user, dialog } = await open();
  await user.click(within(dialog).getByRole("radio", { name: /Dreaming of Electric Snow/ }));
  expect(screen.queryByRole("dialog", { name: "Sound" })).toBeNull();
  expect(album).toHaveBeenCalledWith("play");
  expect(album).not.toHaveBeenCalledWith("toggle");
  const dock = await screen.findByRole("region", { name: `${ALBUM_TITLE} player` });
  expect(dock).toHaveAttribute("data-dock", "docked");
  expect(within(dock).getByText(/Downloading album ·/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Download$/ })).toBeNull();
  act(() => resume("singularity"));
  expect(soundState()).toMatchObject({ playing: false, albumSelected: true });
  await user.click(screen.getByRole("button", { name: /^Choose a soundscape/ }));
  const reopened = screen.getByRole(nativeAlbum() ? "region" : "dialog", { name: "Sound" });
  await user.click(within(reopened).getByRole("radio", { name: /Singularity/ }));
  expect(album).toHaveBeenCalledWith("pausePlayback");
  expect(soundState()).toMatchObject({ playing: false, albumSelected: true });
  await act(async () => finishPause?.());
  await waitFor(() =>
    expect(soundState()).toMatchObject({ playing: true, soundscape: "singularity", albumSelected: false }),
  );
  expect(reopened).not.toBeInTheDocument();
  expect(album).toHaveBeenCalledWith("pausePlayback");
  expect(soundState()).toMatchObject({ playing: true, soundscape: "singularity", albumSelected: false });
  expect(within(reopened).queryByRole("link", { name: "Latent Heinzels" })).toBeNull();
});

it("plays and resumes ordinary audio without spending activation on an inactive album", async () => {
  const album = vi.fn(async (_action: AlbumAction) => ({
    ...downloading,
    phase: "idle" as const,
    title: "",
    playing: false,
  }));
  vi.stubGlobal("heinzelDesktop", { album });
  const { user, dialog } = await open();
  await user.click(within(dialog).getByRole("button", { name: "Play Singularity" }));
  await waitFor(() => expect(soundState()).toMatchObject({ playing: true, albumSelected: false }));
  await user.click(within(dialog).getByRole("button", { name: "Pause Singularity" }));
  await user.click(within(dialog).getByRole("button", { name: "Play Singularity" }));
  expect(soundState()).toMatchObject({ playing: true, albumSelected: false });
  expect(album.mock.calls.every(([action]) => action === "status")).toBe(true);
});

it("does not start ordinary audio when native playback has not actually paused", async () => {
  const album = vi.fn(async (_action: AlbumAction) => downloading);
  vi.stubGlobal("heinzelDesktop", { album });
  const { user, dialog } = await open();
  await user.click(within(dialog).getByRole("radio", { name: /Dreaming of Electric Snow/ }));
  await user.click(screen.getByRole("button", { name: /^Choose a soundscape/ }));
  const reopened = screen.getByRole(nativeAlbum() ? "region" : "dialog", { name: "Sound" });
  await user.click(within(reopened).getByRole("radio", { name: /Singularity/ }));
  await waitFor(() => expect(album).toHaveBeenCalledWith("pausePlayback"));
  expect(soundState()).toMatchObject({ playing: false, albumSelected: true });
});

it("closes the album player and clears selection while saving continues", async () => {
  let status = downloading;
  const album = vi.fn(async (action: AlbumAction) => {
    if (action === "pausePlayback") status = { ...status, playing: false };
    return status;
  });
  vi.stubGlobal("heinzelDesktop", { album });
  const { user } = await open();
  const dock = await screen.findByRole("region", { name: `${ALBUM_TITLE} player` });
  await user.click(within(dock).getByRole("button", { name: "Stop and close the player" }));
  await waitFor(() => expect(screen.queryByRole("region", { name: `${ALBUM_TITLE} player` })).toBeNull());
  expect(soundState()).toMatchObject({ albumSelected: false, playing: false });
  await user.click(screen.getByRole("button", { name: /^Choose a soundscape/ }));
  const reopened = screen.getByRole(nativeAlbum() ? "region" : "dialog", { name: "Sound" });
  expect(within(reopened).getByRole("radio", { name: /Singularity/ })).toHaveAttribute("aria-checked", "true");
  expect(status).toMatchObject({ phase: "downloading", verified: 2, receivedBytes: 200 });
  expect(album).not.toHaveBeenCalledWith("pauseDownload");
  expect(album).not.toHaveBeenCalledWith("cancelDownload");
});

it.each(["idle", "ready", "failed", "paused"] as const)(
  "does not restore a named %s native status on load or remount",
  async (phase) => {
    vi.stubGlobal("heinzelDesktop", {
      album: vi.fn(async () => ({ ...downloading, phase, playing: false, verified: 0, total: 0 })),
    });
    const { view } = await open();
    await waitFor(() => expect(soundState().albumSelected).toBe(false));
    expect(screen.queryByRole("region", { name: `${ALBUM_TITLE} player` })).toBeNull();
    view.unmount();
    resetSound();
    await open();
    expect(soundState().albumSelected).toBe(false);
    expect(screen.queryByRole("region", { name: `${ALBUM_TITLE} player` })).toBeNull();
  },
);

it.each([true, false])("has one upper band link when docked=%s", async (docked) => {
  vi.stubGlobal("heinzelDesktop", { album: vi.fn(async () => downloading) });
  await open();
  act(() => setDocked(docked));
  const player = await screen.findByRole("region", { name: `${ALBUM_TITLE} player` });
  const links = within(player).getAllByRole("link", { name: "Latent Heinzels" });
  expect(links).toHaveLength(1);
  expect(links[0]?.closest("p")).toHaveClass("text-xs");
  expect(links[0]?.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  expect(within(player).queryByRole("button", { name: "Open video" })).toBeNull();
});
it.each(["Off", "Album", "Track"] as const)("labels and styles repeat %s", async (repeatMode) => {
  vi.stubGlobal("heinzelDesktop", { album: vi.fn(async () => ({ ...downloading, repeatMode })) });
  await open();
  const button = await screen.findByRole("button", { name: `Repeat: ${repeatMode}` });
  expect(button).toHaveAttribute("aria-pressed", String(repeatMode !== "Off"));
  expect(button).toHaveClass(repeatMode === "Off" ? "[&_svg]:stroke-[1.75]" : "[&_svg]:stroke-[3]");
  expect(button.querySelector("svg")).not.toBeNull();
});

it("routes album volume to the host and keeps transport playing at zero", async () => {
  let status = { ...downloading, volume: 0.5, effectiveGain: 0.5, muted: false };
  const album = vi.fn(async (action: AlbumAction, options?: { gain: number }) => {
    if (action === "volume" && options) status = { ...status, volume: options.gain, effectiveGain: options.gain };
    return status;
  });
  vi.stubGlobal("heinzelDesktop", { album });
  const { user, dialog } = await open();
  await user.click(within(dialog).getByRole("radio", { name: /Dreaming of Electric Snow/ }));
  const speaker = await screen.findByRole("button", { name: "Choose a soundscape · Playing" });
  expect(speaker.querySelector(".lucide-volume-2")).not.toBeNull();
  await user.click(speaker);
  const slider = screen.getByRole("slider", { name: "Volume" });
  fireEvent.keyDown(slider, { key: "Home" });
  await waitFor(() => expect(album).toHaveBeenCalledWith("volume", { gain: 0 }));
  const muted = await screen.findByRole("button", { name: "Choose a soundscape · Playing · Muted" });
  expect(muted.querySelector(".lucide-volume-x")).not.toBeNull();
  expect(slider).toBeVisible();
  expect(status.playing).toBe(true);
  fireEvent.change(slider, { target: { value: "65" } });
  await waitFor(() => expect(album).toHaveBeenCalledWith("volume", { gain: 0.65 }));
  expect(await screen.findByRole("button", { name: "Choose a soundscape · Playing" })).toBeInTheDocument();
});

it("places the track picker between Next and Repeat and seeks the selected track while downloading", async () => {
  const album = vi.fn(async () => downloading);
  vi.stubGlobal("heinzelDesktop", { album });
  await open();
  const player = await screen.findByRole("region", { name: `${ALBUM_TITLE} player` });
  const controls = within(player).getByRole("group", { name: "Album playback" });
  const picker = within(controls).getByRole("combobox", { name: "Choose album track" });
  expect(picker.parentElement?.previousElementSibling).toBe(
    within(controls).getByRole("button", { name: "Next track" }),
  );
  expect(picker.parentElement?.nextElementSibling).toBe(
    within(controls).getByRole("button", { name: "Repeat: Album" }),
  );
  fireEvent.change(within(player).getByRole("slider", { name: "Seek in track" }), { target: { value: "65" } });
  expect(album).toHaveBeenCalledWith("seek", { trackID: "berlin-morning", seconds: 65 });
  fireEvent.change(picker, { target: { value: "berlin-evening" } });
  expect(album).toHaveBeenCalledWith("select", { trackID: "berlin-evening" });
});
