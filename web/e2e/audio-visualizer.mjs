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
const result = { viewports: [], realSignal: false, realSilence: false, cleanup: false, errors: [] };
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
    const share = pane.getByRole("button", { name: "Share audio", exact: true });
    await share.click();
    await page.waitForFunction(() => document.querySelector("output")?.textContent?.startsWith("Visualizing"));
    assert.equal(await page.evaluate(() => window.audioFixture.options.preferCurrentTab), true);
    const canvas = page.locator("canvas");
    const pixels = () =>
      canvas.evaluate((element) => {
        const { data } = element.getContext("2d").getImageData(0, 0, element.width, element.height);
        const points = [];
        for (let i = 3; i < data.length; i += 4) if (data[i]) points.push(((i - 3) / 4) % element.width);
        return { count: points.length, x: points.reduce((a, b) => a + b, 0) / Math.max(1, points.length) };
      });
    await page.waitForFunction(() => {
      const element = document.querySelector("canvas");
      const { data } = element.getContext("2d").getImageData(0, 0, element.width, element.height);
      let sum = 0,
        count = 0;
      for (let i = 3; i < data.length; i += 4)
        if (data[i]) {
          sum += ((i - 3) / 4) % element.width;
          count += 1;
        }
      return count > 0 && sum / count < element.width / 5;
    });
    const low = await pixels();
    assert(low.count > 0, "Real generated audio must produce nonempty spectrum pixels");
    await page.evaluate(() => {
      window.audioFixture.oscillator.frequency.value = 12000;
    });
    await page.waitForFunction((lowX) => {
      const element = document.querySelector("canvas");
      const { data } = element.getContext("2d").getImageData(0, 0, element.width, element.height);
      let sum = 0,
        count = 0;
      for (let i = 3; i < data.length; i += 4)
        if (data[i]) {
          sum += ((i - 3) / 4) % element.width;
          count += 1;
        }
      return count > 0 && sum / count > lowX + element.width / 4;
    }, low.x);
    assert((await pixels()).x > low.x, "Changing a real tone must move the measured spectrum");
    result.realSignal = true;
    await page.screenshot({ path: join(shots, `audio-${viewport.width}-signal.png`) });
    await page.evaluate(() => {
      window.audioFixture.gain.gain.value = 0;
    });
    await page.waitForFunction(() => document.querySelector("output")?.textContent?.includes("Waiting for sound"));
    // Analyser smoothing decays naturally; the UI does not invent idle bars.
    await page.waitForFunction(() => {
      const element = document.querySelector("canvas");
      return element
        .getContext("2d")
        .getImageData(0, 0, element.width, element.height)
        .data.every((value) => value === 0);
    });
    result.realSilence = true;
    await pane.getByRole("button", { name: "Stop sharing audio" }).click();
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
    await pane.getByRole("button", { name: "Share audio", exact: true }).click();
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
    await pane.getByRole("button", { name: "Share audio", exact: true }).click();
    await page.waitForFunction(() => document.querySelector("output")?.textContent?.includes("cancelled or blocked"));
    await page.screenshot({ path: join(shots, `audio-${viewport.width}-failure.png`) });
    const beforeUnsupported = await page.evaluate(() => {
      delete navigator.mediaDevices.getDisplayMedia;
      return window.audioFixture.requests;
    });
    await pane.getByRole("button", { name: "Share audio", exact: true }).click();
    await page.waitForFunction(() =>
      document.querySelector("output")?.textContent?.includes("Audio sharing is unavailable"),
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
      ).violations.map((item) => item.id),
    );
    assert.deepEqual(violations, []);
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
