// Native tracks on phones (ADR 0166), a section of the mobile sweep: the real server's list and CSP, and a generated
// tone in the media host's place, so nothing reaches the network. Emulation: it can't show lock-screen playback.
import assert from "node:assert/strict";
import { join } from "node:path";

const HOST = "https://media.heinzel.team";
const ELSEWHERE = "https://elsewhere.example";

/** A quiet 440 Hz sine as a WAV file: browsers read the content, so no encoder and no audio file is needed. */
function tone(seconds, rate = 8000) {
  const samples = seconds * rate;
  const file = Buffer.alloc(44 + samples * 2);
  file.write("RIFF", 0);
  file.writeUInt32LE(file.length - 8, 4);
  file.write("WAVEfmt ", 8);
  file.writeUInt32LE(16, 16);
  file.writeUInt16LE(1, 20);
  file.writeUInt16LE(1, 22);
  file.writeUInt32LE(rate, 24);
  file.writeUInt32LE(rate * 2, 28);
  file.writeUInt16LE(2, 32);
  file.writeUInt16LE(16, 34);
  file.write("data", 36);
  file.writeUInt32LE(samples * 2, 40);
  for (let index = 0; index < samples; index++)
    file.writeInt16LE(Math.round(Math.sin((2 * Math.PI * 440 * index) / rate) * 1500), 44 + index * 2);
  return file;
}

/** The media host's stand-in: answers byte ranges as a real one does, which WebKit requires for media. */
async function standIn(context, requests) {
  const body = tone(3);
  await context.route(`${HOST}/**`, async (route) => {
    const headers = await route.request().allHeaders();
    requests.push({ url: route.request().url(), headers });
    const found = /^bytes=(\d+)-(\d*)$/.exec(headers.range ?? "");
    if (!found) return route.fulfill({ body, contentType: "audio/wav", headers: { "accept-ranges": "bytes" } });
    const first = Number(found[1]);
    const last = Math.min(found[2] ? Number(found[2]) : body.length - 1, body.length - 1);
    return route.fulfill({
      status: 206,
      body: body.subarray(first, last + 1),
      contentType: "audio/wav",
      headers: { "accept-ranges": "bytes", "content-range": `bytes ${first}-${last}/${body.length}` },
    });
  });
}

const playing = (page) =>
  page.waitForFunction(
    () => {
      const audio = document.querySelector("audio");
      return audio && !audio.paused && audio.currentTime > 0;
    },
    undefined,
    { timeout: 15_000 },
  );
const still = (page) =>
  page.waitForFunction(() => !document.getAnimations().some((run) => run instanceof CSSTransition));

/** Plays the album in every phone viewport; the first has the whole flow, the others the fullest action row. */
export async function exerciseNativeAudio({ browser, url, shots, cases, assertContained, assertTouchTarget }) {
  for (const [index, { name, width, height }] of cases.entries()) {
    const whole = index === 0;
    const context = await browser.newContext({
      viewport: { width, height },
      isMobile: true,
      hasTouch: true,
      // WebKit doesn't show a request of a worker-controlled page to `route`, so the stand-in would be skipped.
      serviceWorkers: "block",
      // Whatever still isn't routed meets a closed port here instead of the network.
      proxy: { server: "http://127.0.0.1:9", bypass: "127.0.0.1,localhost" },
    });
    const requests = [];
    await standIn(context, requests);
    const unpicked = [];
    await context.route(/^https:\/\/www\.youtube(-nocookie)?\.com\//, (route) => {
      unpicked.push(route.request().url());
      return route.abort();
    });
    const page = await context.newPage();
    if (!whole) {
      // The same album with a stream beside it adds the fallback, so the row is at its widest.
      await page.route("**/api/system/sound/list", async (route) => {
        const response = await route.fetch();
        const list = await response.json();
        const album = list.soundscapes.find((item) => item.name === "fixture-album");
        album.stream = list.soundscapes.find((item) => item.name === "fixture-radio").stream;
        await route.fulfill({ response, json: list });
      });
    }
    await page.goto(url);
    await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
    const expand = page.getByRole("button", { name: "Expand navigation" });
    if (await expand.isVisible()) await expand.tap();
    await still(page);
    await page.getByRole("button", { name: "Choose a soundscape", exact: true }).tap();
    const dialog = page.getByRole("dialog", { name: "Sound", exact: true });
    await dialog.getByText("Plays from media.heinzel.team", { exact: false }).waitFor();
    await assertContained(page, dialog, `${name} sound picker`);
    assert.equal(await page.locator("audio").count(), 0, `${name}: no audio element before Play`);
    assert.equal(requests.length, 0, `${name}: the media host is asked for nothing before Play`);
    await dialog.getByRole("button", { name: "Play Fixture album", exact: true }).tap();
    await playing(page);
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "hidden" });

    const player = page.getByRole("region", { name: "Fixture album player" });
    await player.waitFor();
    await still(page);
    await assertContained(page, player, `${name} native player`);
    const row = player.locator("[data-pane-actions]");
    const actions = await row.getByRole("button").all();
    assert.equal(actions.length, whole ? 3 : 4, `${name}: the card's actions`);
    for (const action of actions) await assertTouchTarget(action, "native player action", name);
    assert.ok(
      await row.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
      `${name}: the action row overflows`,
    );
    await page.screenshot({ path: join(shots, `native-audio-${name}.png`) });

    for (const { url: asked, headers } of requests) {
      assert.ok(asked.startsWith(`${HOST}/fixture/`) && !asked.includes("?"), "only the tracks' own addresses");
      assert.equal(headers.referer, undefined, "no referrer reaches the media host");
      assert.equal(headers.origin, undefined, "the app's address doesn't reach the media host");
    }

    if (whole) {
      assert.ok(!requests.some((request) => request.url.endsWith("/two.mp3")), "the next track isn't preloaded");
      // It ends by itself and the next one plays, with nobody touching the page.
      await player.getByText("Second tone", { exact: true }).waitFor({ timeout: 20_000 });
      await playing(page);
      const named = await page.evaluate(() =>
        "mediaSession" in navigator ? (navigator.mediaSession.metadata?.title ?? "none") : "unsupported",
      );
      assert.equal(named, "Second tone", "the system's controls name the track");
      await player.getByRole("button", { name: "Next track" }).tap();
      await player.getByText("Third tone", { exact: true }).waitFor();
      await playing(page);
      await page.getByRole("button", { name: "Collapse navigation" }).tap();
      await page.getByRole("button", { name: "Pause Fixture album", exact: true }).tap();
      await page.waitForFunction(() => document.querySelector("audio").paused, undefined, { timeout: 10_000 });
      await page.getByRole("button", { name: "Play Fixture album", exact: true }).tap();
      await playing(page);
      assert.equal(await player.isVisible(), false, "the collapsed player doesn't cover the workspace");

      // The page's policy admits that one host for media, and nothing of it for scripts to read.
      const refused = await page.evaluate(
        async ([media, elsewhere]) => {
          const violated = [];
          document.addEventListener("securitypolicyviolation", (event) => violated.push(event.effectiveDirective));
          await fetch(`${media}/fixture/one.mp3`).catch(() => undefined);
          const audio = document.createElement("audio");
          audio.src = `${elsewhere}/track.mp3`;
          audio.load();
          const deadline = Date.now() + 5000;
          while (violated.length < 2 && Date.now() < deadline) await new Promise((done) => setTimeout(done, 50));
          return violated.sort((a, b) => a.localeCompare(b));
        },
        [HOST, ELSEWHERE],
      );
      assert.deepEqual(refused, ["connect-src", "media-src"]);
    }
    assert.deepEqual(unpicked, [], `${name}: YouTube was contacted without being picked`);
    await context.close();
  }
  return { viewports: cases.length, nothingBeforePlay: true, advanced: true, policy: true };
}
