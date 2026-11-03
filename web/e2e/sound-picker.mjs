// The sound picker keeps its place (ADR 0058): open it, pick a soundscape, and it plays at once with the picker
// still open; picking another switches over; Escape and a click outside still close it. Real Chromium against the
// real page, with the sound list and the stream's host replaced by local stand-ins that record what the page asks.
// Usage: node e2e/sound-picker.mjs URL SCREENSHOT_DIR (prints one JSON object)
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const [url, shots] = process.argv.slice(2);
if (!url || !shots) throw new Error("Usage: node e2e/sound-picker.mjs URL SCREENSHOT_DIR");
mkdirSync(shots, { recursive: true });

const ORIGIN = "https://www.youtube-nocookie.com";
const stream = (video) => ({
  provider: "youtube",
  video,
  frame_origin: ORIGIN,
  embed_url: `${ORIGIN}/embed/${video}`,
  watch_url: `https://www.youtube.com/watch?v=${video}`,
});
const soundscapes = [
  { name: "alpha", title: "Alpha FM", stream: stream("aaaaaaaaaaa") },
  { name: "beta", title: "Beta FM", stream: stream("bbbbbbbbbbb") },
  { name: "gamma", title: "Gamma FM", stream: stream("ccccccccccc") },
].map((sound) => ({
  ...sound,
  kind: "stream",
  module: "system.sound",
  plugin: "heinzel",
  selected: false,
  effective: false,
  fade_seconds: 0,
}));

let browser;
try {
  browser = await chromium.launch();
} catch {
  browser = await chromium.launch({ channel: "chrome" });
}
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  // Gamma is configured but not playing at first (Play needs a click): the server's choice moves with each pick, as
  // `hz system sound use` does. All three play from one host, so no page reload comes between a pick and its sound.
  let picked = "gamma";
  const list = () => ({
    selected: picked,
    effective: picked,
    soundscapes: [
      {
        name: "off",
        title: "Off",
        kind: "off",
        module: null,
        plugin: null,
        selected: false,
        effective: false,
        fade_seconds: 0,
      },
      ...soundscapes.map((sound) => ({ ...sound, selected: sound.name === picked, effective: sound.name === picked })),
    ],
    signals: {},
    volume: 50,
    follow_theme: false,
    mute_in_meetings: false,
    duck_while_typing: false,
  });
  await page.route("**/api/system/sound/list", (route) => route.fulfill({ json: list() }));
  await page.route("**/api/system/sound/use**", (route) => {
    picked = new URL(route.request().url()).searchParams.get("name");
    return route.fulfill({ json: list() });
  });
  // The host's player: it announces itself, records every command it gets, and says "playing" to playVideo.
  await page.route(`${ORIGIN}/embed/**`, (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html><body><script>
        window.commands = [];
        addEventListener("message", (event) => {
          const message = JSON.parse(event.data);
          if (message.event === "listening") parent.postMessage(JSON.stringify({ event: "onReady" }), event.origin);
          if (message.event !== "command") return;
          window.commands.push(message.func);
          if (message.func === "playVideo")
            parent.postMessage(JSON.stringify({ event: "onStateChange", info: 1 }), event.origin);
        });
      </script></body></html>`,
    }),
  );
  await page.goto(url);
  await page.getByRole("heading", { name: "Today" }).waitFor();
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
  const speaker = page.getByRole("button", { name: "Choose a soundscape", exact: true });
  const dialog = page.getByRole("dialog", { name: "Sound", exact: true });
  const commands = async (title) => {
    const frame = await page
      .getByRole("region", { name: `${title} player` })
      .locator("iframe")
      .elementHandle();
    return (await frame.contentFrame()).evaluate(() => window.commands);
  };

  await speaker.click();
  await dialog.waitFor();
  assert.equal(await page.getByRole("region", { name: /player$/ }).count(), 0, "nothing plays before the pick");
  // The first thing the person does on this page is pick: that click is the gesture the player starts inside.
  await dialog.getByRole("radio", { name: /^Alpha FM/ }).click();

  // 1. The picker is still open, and the sound plays, with no second click.
  await dialog.waitFor({ state: "visible" });
  await dialog.getByRole("button", { name: "Pause Alpha FM", exact: true }).waitFor();
  await page.getByRole("region", { name: "Alpha FM player" }).waitFor();
  await page.waitForTimeout(400);
  await shotAfter("picked");
  assert.ok((await commands("Alpha FM")).includes("playVideo"), "the player was told to play inside the pick");
  assert.equal(await dialog.getByRole("radio", { name: /^Alpha FM/ }).getAttribute("aria-checked"), "true");
  assert.ok(await page.evaluate(() => navigator.userActivation.hasBeenActive), "a gesture started it");
  assert.equal(await page.locator('[role="radio"]:focus').count(), 1, "the keyboard focus stays on the pick");

  // 2. Picking another switches over: the new player plays, the old one is gone, the picker is still there.
  await dialog.getByRole("radio", { name: /^Beta FM/ }).click();
  await dialog.getByRole("button", { name: "Pause Beta FM", exact: true }).waitFor();
  await page.getByRole("region", { name: "Beta FM player" }).waitFor();
  await page.waitForTimeout(400);
  assert.ok((await commands("Beta FM")).includes("playVideo"));
  assert.equal(await page.getByRole("region", { name: "Alpha FM player" }).count(), 0);
  assert.equal(await dialog.isVisible(), true);

  // 3. The header's Pause pauses the current one and says so; the speaker shows it too.
  await dialog.getByRole("button", { name: "Pause Beta FM", exact: true }).click();
  await dialog.getByRole("button", { name: "Play Beta FM", exact: true }).waitFor();
  const icon = () =>
    speaker.locator("svg").evaluate((svg) => [...svg.classList].find((name) => name.startsWith("lucide-volume")));
  assert.equal(await icon(), "lucide-volume-x", "the speaker shows muted");
  await dialog.getByRole("button", { name: "Play Beta FM", exact: true }).click();
  await dialog.getByRole("button", { name: "Pause Beta FM", exact: true }).waitFor();
  assert.equal(await icon(), "lucide-volume-2", "the speaker shows playing");

  // 4. Escape closes it and returns the focus to the speaker; so does a click outside. The sound plays on.
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  assert.equal(
    await speaker.evaluate((element) => element === document.activeElement),
    true,
    "focus returns to the speaker",
  );
  await speaker.click();
  await dialog.waitFor();
  await page.mouse.click(700, 450);
  await dialog.waitFor({ state: "hidden" });
  await page.getByRole("region", { name: "Beta FM player" }).waitFor();
  assert.equal(await icon(), "lucide-volume-2");
  console.log(JSON.stringify({ stayedOpen: true, played: true, switched: true, closes: ["Escape", "outside"] }));

  async function shotAfter(name) {
    await page.screenshot({ path: join(shots, `sound-picker-${name}.png`) });
  }
} finally {
  await browser.close();
}
