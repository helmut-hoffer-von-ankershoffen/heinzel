import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { shell } from "@/test/fixtures";
import { mockApi } from "@/test/render";
import { renderAt } from "@/test/renderApp";
import { ALBUM_TITLE, type AlbumAction, type AlbumSnapshot } from "./album";
import { resetDock } from "./dock";
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
  await user.click(await screen.findByRole("button", { name: "Choose a soundscape" }));
  return { user, view, dialog: screen.getByRole("dialog", { name: "Sound" }) };
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
  await user.click(screen.getByRole("button", { name: "Choose a soundscape" }));
  await user.click(
    within(screen.getByRole("dialog", { name: "Sound" })).getByRole("radio", { name: /Dreaming of Electric Snow/ }),
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
  expect(within(dock).getByRole("button", { name: /Choose album track/ })).toHaveTextContent("Berlin Morning");
  expect(within(dock).getAllByText(ALBUM_TITLE)).toHaveLength(1);
  expect(within(dock).queryByText("200 / 800 bytes")).toBeNull();
  expect(within(dock).getByText(/2\/37 verified/)).toBeInTheDocument();
  expect(within(dock).getByRole("progressbar", { name: "Album download" })).toHaveAttribute("value", "200");
  expect(within(dock).getByRole("progressbar", { name: "Album download" })).toHaveAttribute(
    "aria-valuetext",
    "200 / 800 bytes",
  );
  status = { ...downloading, playing: false, title: "Berlin Evening", repeatMode: "Track", verified: 7 };
  act(() => window.dispatchEvent(new Event("heinzel-album-change")));
  expect(await within(dock).findByText("Paused · Berlin Evening")).toBeInTheDocument();
  expect(within(dock).getByRole("button", { name: /Choose album track/ })).toHaveTextContent("Berlin Evening");
  expect(within(dock).getByText(/7\/37 verified/)).toBeInTheDocument();
  expect(within(dock).getByRole("button", { name: "Repeat: Track" })).not.toHaveAttribute("aria-pressed");
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("button", { name: "Choose a soundscape" }));
  expect(
    within(screen.getByRole("dialog", { name: "Sound" })).getByRole("radio", { name: /Singularity/ }),
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
    ["Choose album track: Berlin Morning", "picker"],
    ["Open video", "openVideo"],
  ]) {
    await user.click(within(dock).getByRole("button", { name }));
    expect(album).toHaveBeenCalledWith(action);
  }
  await user.click(within(dock).getByRole("button", { name: "Pause Album" }));
  expect(await within(dock).findByText(/downloading ·/)).toBeInTheDocument();
  await user.click(within(dock).getByRole("button", { name: "Play Album" }));
  await user.click(within(dock).getByRole("button", { name: "Pause download" }));
  expect(await within(dock).findByText("Playing · Berlin Morning")).toBeInTheDocument();
  await user.click(await within(dock).findByRole("button", { name: "Resume download" }));
  expect(album).toHaveBeenCalledWith("resumeDownload");
  await user.click(await within(dock).findByRole("button", { name: "Cancel download (keep verified files)" }));
  expect(album).toHaveBeenCalledWith("cancelDownload");
  expect(await within(dock).findByText("Playing · Berlin Morning")).toBeInTheDocument();
  expect(within(dock).getByText(/2\/37 verified/)).toBeInTheDocument();
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
  expect(within(dock).getByText(/downloading ·/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Download$/ })).toBeNull();
  act(() => resume("singularity"));
  expect(soundState()).toMatchObject({ playing: false, albumSelected: true });
  await user.click(screen.getByRole("button", { name: "Choose a soundscape" }));
  const reopened = screen.getByRole("dialog", { name: "Sound" });
  await user.click(within(reopened).getByRole("radio", { name: /Singularity/ }));
  expect(album).toHaveBeenCalledWith("pausePlayback");
  expect(soundState()).toMatchObject({ playing: false, albumSelected: true });
  await act(async () => finishPause?.());
  await waitFor(() =>
    expect(soundState()).toMatchObject({ playing: true, soundscape: "singularity", albumSelected: false }),
  );
  expect(within(reopened).getByRole("radio", { name: /Singularity/ })).toHaveAttribute("aria-checked", "true");
  expect(album).toHaveBeenCalledWith("pausePlayback");
  expect(soundState()).toMatchObject({ playing: true, soundscape: "singularity", albumSelected: false });
  expect(within(reopened).getByRole("slider", { name: "Volume" })).toHaveValue("50");
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
  await user.click(screen.getByRole("button", { name: "Choose a soundscape" }));
  const reopened = screen.getByRole("dialog", { name: "Sound" });
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
  await user.click(screen.getByRole("button", { name: "Choose a soundscape" }));
  const reopened = screen.getByRole("dialog", { name: "Sound" });
  expect(within(reopened).getByRole("radio", { name: /Singularity/ })).toHaveAttribute("aria-checked", "true");
  expect(status).toMatchObject({ phase: "downloading", verified: 2, receivedBytes: 200 });
  expect(album).not.toHaveBeenCalledWith("pauseDownload");
  expect(album).not.toHaveBeenCalledWith("cancelDownload");
});

it("measures the current album dock on each Watch without changing playback", async () => {
  const album = vi.fn(async () => downloading);
  vi.stubGlobal("heinzelDesktop", { album });
  const { user } = await open();
  const controls = await screen.findByRole("region", { name: "Album controls" });
  const measure = vi.spyOn(controls, "getBoundingClientRect");
  for (const width of [256, 340, 210]) {
    measure.mockReturnValue(new DOMRect(0, 0, width, 160));
    await user.click(within(controls).getByRole("button", { name: "Open video" }));
    expect(album).toHaveBeenLastCalledWith("openVideo", { width, viewportWidth: window.innerWidth });
  }
  expect(album.mock.calls.flat()).not.toContain("pausePlayback");
  measure.mockRestore();
});
