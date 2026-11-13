// Real Chromium Web Audio and canvas. Only the picker transport is replaced with a generated MediaStream.
// Usage: node e2e/audio-visualizer.mjs SCREENSHOT_DIR
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright-core";
import { createServer } from "vite";

const [shots] = process.argv.slice(2);
mkdirSync(shots, { recursive: true });
const require = createRequire(import.meta.url);
const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
// Its own dependency cache, and the harness page scanned before the first request: other journeys start Vite servers
// in parallel on CI, and a shared cold cache re-optimized by one of them answers this page "504 Outdated Optimize Dep".
const server = await createServer({
  cacheDir: mkdtempSync(join(tmpdir(), "vite-audio-")),
  optimizeDeps: { entries: ["e2e/audio-visualizer.html"] },
  server: { host: "127.0.0.1", port: 0, strictPort: false },
});
await server.listen();
const address = server.httpServer.address();
assert(address && typeof address !== "string");
let browser;
// Measured 0.30 -> 0.71..0.74 for 440 Hz -> 12 kHz in Chromium at both widths; 0.2 is half the smallest move.
const RAIL_MARGIN = 0.2;
const result = { viewports: [], tones: [], realSignal: false, realSilence: false, cleanup: false, errors: [] };
// For a failure: the page in use, its console errors and failed requests, written out before the error is rethrown.
let open = null;
const consoleErrors = [];
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
    open = { page, viewport };
    page.on("pageerror", (error) => result.errors.push(String(error)));
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(`${viewport.width}px: ${message.text()}`);
    });
    page.on("requestfailed", (request) =>
      consoleErrors.push(`${viewport.width}px: request failed ${new URL(request.url()).pathname}`),
    );
    await page.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      if (url.hostname === "127.0.0.1" && url.port === String(address.port) && !url.pathname.startsWith("/api/"))
        return route.continue();
      result.errors.push("Unexpected non-fixture browser request");
      return route.abort();
    });
    // Diagnosis only: lost and restored canvas contexts, and whether the page still renders frames at all.
    await page.addInitScript(() => {
      const seen = { lost: 0, restored: 0, frames: 0 };
      window.canvasEvents = seen;
      document.addEventListener("contextlost", () => (seen.lost += 1), true);
      document.addEventListener("contextrestored", () => (seen.restored += 1), true);
      const tick = () => {
        seen.frames += 1;
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
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
    // Installed in the page once, so a wait can return the very frame it accepted (see `until`).
    await page.evaluate(() => {
      window.vizSnapshot = () => {
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
          count = 0,
          strong = 0;
        for (let i = 0; i < data.length; i += 4)
          if (data[i + 3]) {
            share += data[i + 1] / (data[i] + data[i + 1] + data[i + 2] + 1);
            count += 1;
            if (data[i + 3] >= 32) strong += 1;
          }
        return {
          centroid: Number(rail.dataset.vizCentroid ?? "-1"),
          sampleRate: Number(rail.dataset.vizSampleRate ?? "0"),
          rail: height / Math.max(1, mass),
          count,
          // The share of the window painted at an alpha a person can see (1/8 or more), not just nonzero.
          visible: strong / (data.length / 4),
          share: share / Math.max(1, count),
          // The generator's audio clock against the page clock (seconds): when the generated stream stalls, the
          // audio clock falls behind and the failure log names a starved signal instead of a drawing problem.
          canvas: {
            ...window.canvasEvents,
            skyLost: sky.getContext("2d").isContextLost?.() ?? null,
            visibility: document.visibilityState,
          },
          clock: {
            audio: Number((window.audioFixture.contexts.at(-1)?.currentTime ?? -1).toFixed(2)),
            page: Number((performance.now() / 1000).toFixed(2)),
          },
        };
      };
    });
    const measure = () => page.evaluate(() => window.vizSnapshot());
    const allStopped = () =>
      page.evaluate(() =>
        window.audioFixture.streams.every((stream) =>
          stream.getTracks().every((track) => track.readyState === "ended"),
        ),
      );
    const audit = () =>
      page.evaluate(async () =>
        (
          await window.axe.run(document, {
            runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
          })
        ).violations.map((item) => ({
          id: item.id,
          nodes: item.nodes.map((node) => ({ target: node.target, summary: node.failureSummary })),
        })),
      );
    // Fails with what was measured, so a CI log tells a dead analyser from a drawing or timing problem.
    // Returns the predicate's truthy value: a snapshot it accepted is the frame the assertions then read.
    const until = async (what, predicate, arg) => {
      try {
        return await (await page.waitForFunction(predicate, arg, { timeout: 30_000, polling: 100 })).jsonValue();
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
    assert(low.count > 0, `${viewport.width}px: real generated audio must paint the aurora: ${JSON.stringify(low)}`);
    // One plain tone fills a single band: it must still light the window visibly, not paint a trace nobody sees.
    assert(
      low.visible > 0.03,
      `${viewport.width}px: a single tone must paint a visible aurora, not a faint trace: ${JSON.stringify(low)}`,
    );
    await page.evaluate(() => {
      window.audioFixture.oscillator.frequency.value = 12000;
    });
    // Wait until the capture has heard the moved tone, not for page time: the centroid stands at least half the
    // spectrum above the 440 Hz reading (it settles 0.6 above it), so the 12 kHz tone dominates what was analysed.
    // Then the assertions read that very frame. A centroid that had just crossed a quarter, then 500 ms of page time,
    // read a frame with no aurora at all and the centroid at 0.705 on a loaded macOS runner (run 43510033780): the
    // capture had stopped hearing the generated tone for a while, and the aurora fades with silence by design.
    const high = await until(
      `the 12 kHz tone did not reach the capture and move the measured spectrum up from ${JSON.stringify(low)}`,
      (from) => {
        const now = window.vizSnapshot();
        return now.centroid > from.centroid + 0.5 ? now : null;
      },
      low,
    );
    result.tones.push({ width: viewport.width, low, high });
    assert(
      high.count > 0,
      `${viewport.width}px: the moved tone must still paint the aurora: ${JSON.stringify({ low, high })}`,
    );
    // Measured 0.002 (1440) and 0.011 (390) while loudness was the mean over all bands, 0.09 and 0.12 since: the
    // threshold sits a factor of three from both, for other renderers.
    assert(
      high.visible > 0.03,
      `${viewport.width}px: a single high tone must paint a visible aurora: ${JSON.stringify(high)}`,
    );
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
    const away = { x: 260, y: Math.round(viewport.height / 3), width: Math.min(80, viewport.width - 260), height: 200 };
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
    // Show is pick-any: unchecking the Halo chip takes the Halo away, checking it brings it back.
    const haloChip = pane.getByRole("checkbox", { name: "Halo", exact: true });
    // The chip is the label a person presses; its native checkbox is visually hidden.
    const haloLabel = pane.locator("label").filter({ hasText: /^Halo$/ });
    await haloLabel.click();
    assert.equal(await haloChip.isChecked(), false);
    await page.locator("canvas[data-testid=audio-halo]").waitFor({ state: "detached" });
    await haloLabel.click();
    assert.equal(await haloChip.isChecked(), true);
    await page.locator("canvas[data-testid=audio-halo]").waitFor();
    const swarmChip = pane.getByRole("checkbox", { name: "Swarm", exact: true });
    const swarmLabel = pane.locator("label").filter({ hasText: /^Swarm$/ });
    await swarmLabel.click();
    assert.equal(await swarmChip.isChecked(), false);
    await page.locator("canvas[data-testid=audio-swarm]").waitFor({ state: "detached" });
    assert.equal(await page.locator("[data-swarm-figure]").count(), 0);
    await swarmLabel.click();
    assert.equal(await swarmChip.isChecked(), true);
    await page.locator("canvas[data-testid=audio-swarm]").waitFor();
    await page.waitForTimeout(400);
    const playing = await heinzels();
    assert(
      playing.halo && playing.figures === 3,
      `${viewport.width}px: Halo and Swarm must stand: ${JSON.stringify(playing)}`,
    );
    // The portal must belong to the real Sidebar, immediately before its real PlayerCard dock, not a fixture stand-in.
    const geometry = await page.evaluate(() => {
      const sidebar = document.querySelector('aside[aria-label="Main"]');
      const stage = sidebar?.querySelector("[data-viz-stage]");
      const dock = sidebar?.querySelector("[data-sound-dock]");
      const card = document.querySelector('[data-dock="docked"]');
      if (!stage || !dock || !card || !stage.contains(document.querySelector('[data-testid="audio-halo"]')))
        return null;
      const s = stage.getBoundingClientRect(),
        d = dock.getBoundingClientRect(),
        p = card.getBoundingClientRect();
      return {
        ordered: Boolean(stage.compareDocumentPosition(dock) & Node.DOCUMENT_POSITION_FOLLOWING),
        stageTop: s.top,
        stageBottom: s.bottom,
        stageWidth: s.width,
        stageHeight: s.height,
        haloHeight: document.querySelector('[data-testid="audio-halo"]').getBoundingClientRect().height,
        figureHeight: document.querySelector("[data-swarm-figure]").getBoundingClientRect().height,
        navBottom: sidebar.querySelector("nav").getBoundingClientRect().bottom,
        dockTop: d.top,
        cardTop: p.top,
        cardWidth: p.width,
      };
    });
    assert(
      geometry?.ordered &&
        geometry.stageWidth > 0 &&
        geometry.stageBottom <= geometry.dockTop + 1 &&
        Math.abs(geometry.dockTop - geometry.cardTop) <= 2 &&
        geometry.cardWidth > 0 &&
        geometry.stageTop >= 0,
      `${viewport.width}px: real Sidebar stage and player must be visible and not overlap: ${JSON.stringify(geometry)}`,
    );
    // The stage fills the gap the navigation leaves (20 rem at most), not an 80 px strip: the Halo above, the Swarm
    // on the floor, both far larger than on the strip, and the navigation keeps its own room above them.
    assert(
      geometry.stageHeight >= 300 &&
        geometry.stageHeight <= 321 &&
        geometry.haloHeight >= 150 &&
        geometry.figureHeight >= 70 &&
        geometry.navBottom <= geometry.stageTop + 1,
      `${viewport.width}px: Halo and Swarm must fill the sidebar's gap: ${JSON.stringify(geometry)}`,
    );
    await page.waitForFunction(() =>
      [...document.querySelectorAll("[data-swarm-figure]")].some((figure) => figure.style.transform),
    );
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.waitForFunction(() => {
      const figures = [...document.querySelectorAll("[data-swarm-figure]")];
      return figures.length === 3 && figures.every((figure) => !figure.style.transform);
    });
    await page.emulateMedia({ reducedMotion: "no-preference" });
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
    assert(await allStopped(), "Every generated stream must be stopped");
    result.cleanup = true;
    await page.evaluate(() => {
      window.audioFixture.mode = "no-audio";
    });
    await pane.getByRole("radio", { name: "Shared audio", exact: true }).click();
    await page.waitForFunction(() => document.querySelector("output")?.textContent?.includes("No audio was shared"));
    assert(await pane.getByRole("status").isVisible());
    assert(await allStopped(), "Every generated stream must be stopped");
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
    // Not offered any more: the choice leaves the group and one line says why.
    await pane.getByText("Shared audio is not offered by this browser.", { exact: true }).waitFor();
    assert.equal(await pane.getByRole("radio", { name: "Shared audio", exact: true }).count(), 0);
    // The fixture offers no microphone either, so nothing is left to choose: no group, not a lone "Off".
    assert.equal(await pane.getByRole("radiogroup", { name: "Listen to" }).count(), 0);
    assert.equal(await pane.getByRole("radio", { name: "Microphone", exact: true }).count(), 0);
    assert.equal(await page.evaluate(() => window.audioFixture.requests), beforeUnsupported);
    const bounds = await pane.boundingBox();
    assert(
      bounds.x >= 0 &&
        bounds.y >= 0 &&
        bounds.x + bounds.width <= viewport.width &&
        bounds.y + bounds.height <= viewport.height,
    );
    await page.evaluate(axe);
    const violations = await audit();
    assert.deepEqual(violations, [], `${viewport.width}px axe: ${JSON.stringify(violations)}`);
    // And in dark mode: the chip and segment fills keep their contrast.
    await page.evaluate(() => {
      localStorage.setItem("hz.theme", "dark");
      window.dispatchEvent(new Event("hz:mode"));
    });
    await page.waitForFunction(
      () =>
        document.documentElement.classList.contains("dark") &&
        getComputedStyle(document.body).color === "rgb(233, 237, 229)" &&
        Number(getComputedStyle(document.querySelector('[data-testid="heinzel-wordmark"]')).color.match(/\d+/)?.[0]) >
          200,
    );
    const dark = await audit();
    await page.evaluate(() => {
      localStorage.setItem("hz.theme", "light");
      window.dispatchEvent(new Event("hz:mode"));
    });
    assert.deepEqual(dark, [], `${viewport.width}px axe, dark: ${JSON.stringify(dark)}`);
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
} catch (error) {
  await diagnose();
  throw error;
} finally {
  await browser?.close();
  await server.close();
}

/** Write what the failing page showed to stderr and a screenshot next to the others (CI keeps them as an artifact). */
async function diagnose() {
  const lines = [`page errors: ${JSON.stringify(result.errors)}`, `console errors: ${JSON.stringify(consoleErrors)}`];
  if (open && !open.page.isClosed()) {
    const { page, viewport } = open;
    lines.push(`viewport: ${viewport.width}x${viewport.height}`);
    const region = await page
      .evaluate(
        () => document.querySelector('section[aria-label="Audio visualizer"]')?.outerHTML.slice(0, 2000) ?? "none",
      )
      .catch((failure) => `unreadable: ${failure}`);
    lines.push(`visualizer region: ${region}`);
    await page
      .screenshot({ path: join(shots, `audio-${viewport.width}-diagnostic.png`), fullPage: true })
      .catch((failure) => lines.push(`no screenshot: ${failure}`));
  }
  process.stderr.write(`${lines.join("\n")}\n`);
}
