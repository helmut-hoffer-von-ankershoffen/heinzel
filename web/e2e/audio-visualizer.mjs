// Real Chromium Web Audio and canvas. Only the picker transport is replaced with a generated MediaStream.
// Usage: node e2e/audio-visualizer.mjs SCREENSHOT_DIR
import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const [shots] = process.argv.slice(2);
mkdirSync(shots, { recursive: true });
const require = createRequire(import.meta.url);
const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const server = await createServer({ server: { host: "127.0.0.1", port: 0, strictPort: false } });
await server.listen();
const address = server.httpServer.address();
assert(address && typeof address !== "string");
let browser;
// Measured 0.30 -> 0.71..0.74 for 440 Hz -> 12 kHz in Chromium at both widths; 0.2 is half the smallest move.
const RAIL_MARGIN = 0.2;
const result = { viewports: [], tones: [], realSignal: false, realSilence: false, cleanup: false, errors: [] };
try {
  try {
    browser = await chromium.launch();
  } catch {
    browser = await chromium.launch({ channel: "chrome" });
  }
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ]) {
    const page = await browser.newPage({ viewport });
    page.on("pageerror", (error) => result.errors.push(String(error)));
    await page.goto(`http://127.0.0.1:${address.port}/e2e/audio-visualizer.html`);
    const trigger = page
      .getByRole("region", { name: "Audio visualizer", exact: true })
      .getByRole("button", { name: /^Audio / })
      .first();
    await trigger.waitFor();
    assert.equal(await page.evaluate(() => window.audioFixture.requests), 0);
    const target = await trigger.boundingBox();
    assert(target.width >= 44 && target.height >= 44, "Audio control must be discoverable and touchable");
    await trigger.click();
    const pane = page.getByRole("region", { name: "Audio visualizer controls" });
    await pane.waitFor();
    assert.equal(await page.evaluate(() => window.audioFixture.requests), 0, "Opening help must not capture");
    const share = pane.getByRole("radio", { name: "Shared audio", exact: true });
    await share.click();
    await page.waitForFunction(() => document.querySelector("output")?.textContent?.startsWith("Visualizing"));
    assert.equal(await page.evaluate(() => window.audioFixture.options.preferCurrentTab), true);
    // What the visualizer measured and drew, read in the page: the shared analysis' spectrum position (0 = 40 Hz,
    // 1 = 12 kHz, -1 = silent), where the Spectrum rail's painted mass sits (0 = bottom/bass, 1 = top/treble), and
    // the aurora's painted pixel count and mean green share (logged for diagnosis, not asserted: its animation drifts).
    const measure = () =>
      page.evaluate(() => {
        const rail = document.querySelector('canvas[aria-label="Shared audio spectrum"]');
        const sky = document.querySelector("canvas[data-testid=audio-aurora]");
        let mass = 0,
          height = 0;
        const pixels = rail.getContext("2d").getImageData(0, 0, rail.width, rail.height).data;
        for (let i = 3; i < pixels.length; i += 4) {
          const row = Math.floor((i - 3) / 4 / rail.width);
          mass += pixels[i];
          height += pixels[i] * (1 - row / Math.max(1, rail.height - 1));
        }
        const { data } = sky.getContext("2d").getImageData(0, 0, sky.width, sky.height);
        let share = 0,
          count = 0;
        for (let i = 0; i < data.length; i += 4)
          if (data[i + 3]) {
            share += data[i + 1] / (data[i] + data[i + 1] + data[i + 2] + 1);
            count += 1;
          }
        return {
          centroid: Number(rail.dataset.vizCentroid ?? "-1"),
          sampleRate: Number(rail.dataset.vizSampleRate ?? "0"),
          rail: height / Math.max(1, mass),
          count,
          share: share / Math.max(1, count),
        };
      });
    // Fails with what was measured, so a CI log tells a dead analyser from a drawing or timing problem.
    const until = async (what, predicate, arg) => {
      try {
        await page.waitForFunction(predicate, arg, { timeout: 30_000, polling: 100 });
      } catch (error) {
        throw new Error(`${viewport.width}px: ${what}; measured ${JSON.stringify(await measure())}`, { cause: error });
      }
    };
    await until("generated audio never reached the analysis and the aurora", () => {
      const rail = document.querySelector('canvas[aria-label="Shared audio spectrum"]');
      const sky = document.querySelector("canvas[data-testid=audio-aurora]");
      if (!rail || !sky || Number(rail.dataset.vizCentroid ?? "-1") < 0) return false;
      const { data } = sky.getContext("2d").getImageData(0, 0, sky.width, sky.height);
      return data.some((value, index) => index % 4 === 3 && value > 0);
    });
    // Let the 440 Hz tone settle, then move it to 12 kHz: the measured spectrum position must climb by at least a
    // quarter of the spectrum (as the old strip's centroid had to move a quarter of its width), and the rail's
    // painted mass must move up with it.
    await page.waitForTimeout(1500);
    const low = await measure();
    assert(low.count > 0, "Real generated audio must produce nonempty aurora pixels");
    await page.evaluate(() => {
      window.audioFixture.oscillator.frequency.value = 12000;
    });
    await until(
      `the 12 kHz tone did not move the measured spectrum up from ${JSON.stringify(low)}`,
      (lowCentroid) =>
        Number(document.querySelector('canvas[aria-label="Shared audio spectrum"]').dataset.vizCentroid ?? "-1") >
        lowCentroid + 0.25,
      low.centroid,
    );
    await page.waitForTimeout(500);
    const high = await measure();
    result.tones.push({ width: viewport.width, low, high });
    assert(high.count > 0, "The moved tone must still paint the aurora");
    assert(
      high.centroid > low.centroid + 0.25,
      `Changing a real tone must move the measured spectrum: ${JSON.stringify({ low, high })}`,
    );
    assert(
      high.rail > low.rail + RAIL_MARGIN,
      `Changing a real tone must move the drawn spectrum up the rail: ${JSON.stringify({ low, high })}`,
    );
    result.realSignal = true;
    // What the person sees: a region of the page away from the controls must change while the aurora paints and
    // return once it clears. A painted <body> (index.html gives <html> its own background) hid the aurora completely.
    const away = { x: 40, y: Math.round(viewport.height / 3), width: Math.round(viewport.width / 4), height: 200 };
    const lit = await page.screenshot({ clip: away });
    // The Heinzel styles, from the same signal: the Halo's rays are drawn, and the Swarm's figures move.
    const heinzels = () =>
      page.evaluate(() => {
        const halo = document.querySelector("canvas[data-testid=audio-halo]");
        let ink = 0;
        if (halo)
          for (const [index, value] of halo.getContext("2d").getImageData(0, 0, halo.width, halo.height).data.entries())
            if (index % 4 === 3) ink += value;
        const moving = [...document.querySelectorAll("[data-swarm-figure]")].filter((figure) => figure.style.transform);
        return {
          halo: Boolean(halo),
          ink,
          figures: document.querySelectorAll("[data-swarm-figure]").length,
          moving: moving.length,
        };
      });
    const playing = await heinzels();
    assert(
      playing.halo && playing.figures === 3,
      `${viewport.width}px: Halo and Swarm must stand: ${JSON.stringify(playing)}`,
    );
    await page.waitForFunction(() =>
      [...document.querySelectorAll("[data-swarm-figure]")].some((figure) => figure.style.transform),
    );
    await page.screenshot({ path: join(shots, `audio-${viewport.width}-signal.png`) });
    await page.evaluate(() => {
      window.audioFixture.gain.gain.value = 0;
    });
    await page.waitForFunction(() => document.querySelector("output")?.textContent?.includes("Waiting for sound"));
    // Analyser smoothing decays naturally; the UI does not invent idle bars.
    await page.waitForFunction(() => {
      const element = document.querySelector("canvas[data-testid=audio-aurora]");
      return element
        .getContext("2d")
        .getImageData(0, 0, element.width, element.height)
        .data.every((value) => value === 0);
    });
    const resting = await heinzels();
    assert(
      resting.moving === 0 && playing.ink > resting.ink * 1.5,
      `${viewport.width}px: the Halo must grow with the sound and the Swarm stand still in silence: ${JSON.stringify({ playing, resting })}`,
    );
    assert(
      !lit.equals(await page.screenshot({ clip: away })),
      `${viewport.width}px: the aurora painted but nothing on the page changed: it is hidden under another layer`,
    );
    result.realSilence = true;
    await pane.getByRole("radio", { name: "Off", exact: true }).click();
    assert(
      await page.evaluate(() =>
        window.audioFixture.streams.every((stream) =>
          stream.getTracks().every((track) => track.readyState === "ended"),
        ),
      ),
    );
    result.cleanup = true;
    await page.evaluate(() => {
      window.audioFixture.mode = "no-audio";
    });
    await pane.getByRole("radio", { name: "Shared audio", exact: true }).click();
    await page.waitForFunction(() => document.querySelector("output")?.textContent?.includes("No audio was shared"));
    assert(await pane.getByRole("status").isVisible());
    assert(
      await page.evaluate(() =>
        window.audioFixture.streams.every((stream) =>
          stream.getTracks().every((track) => track.readyState === "ended"),
        ),
      ),
    );
    await page.evaluate(() => {
      window.audioFixture.mode = "deny";
    });
    await pane.getByRole("radio", { name: "Shared audio", exact: true }).click();
    await page.waitForFunction(() => document.querySelector("output")?.textContent?.includes("cancelled or blocked"));
    await page.screenshot({ path: join(shots, `audio-${viewport.width}-failure.png`) });
    const beforeUnsupported = await page.evaluate(() => {
      delete navigator.mediaDevices.getDisplayMedia;
      return window.audioFixture.requests;
    });
    await pane.getByRole("radio", { name: "Shared audio", exact: true }).click();
    await page.waitForFunction(() =>
      document.querySelector("output")?.textContent?.includes("Shared audio is not offered by this browser"),
    );
    assert.equal(await page.evaluate(() => window.audioFixture.requests), beforeUnsupported);
    const bounds = await pane.boundingBox();
    assert(
      bounds.x >= 0 &&
        bounds.y >= 0 &&
        bounds.x + bounds.width <= viewport.width &&
        bounds.y + bounds.height <= viewport.height,
    );
    await page.evaluate(axe);
    const violations = await page.evaluate(async () =>
      (
        await window.axe.run(document, {
          runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
        })
      ).violations.map((item) => ({
        id: item.id,
        nodes: item.nodes.map((node) => ({ target: node.target, summary: node.failureSummary })),
      })),
    );
    assert.deepEqual(violations, [], `${viewport.width}px axe: ${JSON.stringify(violations)}`);
    await page.keyboard.press("Escape");
    await pane.waitFor({ state: "hidden" });
    assert(await trigger.evaluate((element) => element === document.activeElement));
    await page.evaluate(async () => {
      await Promise.all(window.audioFixture.contexts.map((context) => context.close()));
    });
    await page.close();
    result.viewports.push(viewport.width);
  }
  assert.deepEqual(result.errors, []);
  console.log(JSON.stringify(result));
} finally {
  await browser?.close();
  await server.close();
}
