// An installed window opened while hz is down shows the offline page, and opens the app by itself once hz is back:
// no reload, navigation or restart from the person (here: from this driver) after hz starts again.
// node pwa-reconnect.mjs ENGINE PROFILE_DIR LINK ORIGIN
// Talks to its parent on stdout/stdin: READY / "stopped" / DOWN / "started" -> final JSON.
import { createInterface } from "node:readline";
import { chromium, webkit } from "playwright-core";
import { until } from "./until.mjs";

const [engineName, profile, link, origin] = process.argv.slice(2);
const engine = engineName === "webkit" ? webkit : chromium;
const input = createInterface({ input: process.stdin });
const lines = input[Symbol.asyncIterator]();
const next = async (expected) => {
  const { value } = await lines.next();
  if (value?.trim() !== expected) throw new Error(`expected "${expected}" from the parent, got "${value}"`);
};
const began = Date.now();
const note = (event) => console.error(`${String(Date.now() - began).padStart(6)} ms  ${event}`);
const context = await engine.launchPersistentContext(profile, {
  viewport: { width: 390, height: 700 },
  serviceWorkers: "allow",
});
try {
  const page = context.pages()[0] ?? (await context.newPage());
  page.on("console", (message) => message.type() === "error" && note(`console error: ${message.text()}`));
  const ready = page.getByRole("contentinfo");

  await page.goto(link);
  await ready.waitFor({ timeout: 20000 });
  await until(() => page.evaluate(() => localStorage.getItem("hz.device")), {
    timeout: 15000,
    message: "The device was never paired",
  });
  await page.goto(origin); // the pairing link is gone from the address, as in an installed window
  await ready.waitFor({ timeout: 20000 });
  // The worker controls the page and has kept the offline page.
  await until(
    () =>
      page.evaluate(
        async () => Boolean(navigator.serviceWorker.controller) && Boolean(await caches.match("/offline.html")),
      ),
    { timeout: 20000, message: "The service worker never took control with the offline page cached" },
  );
  console.log("READY");

  await next("stopped");
  note("hz stopped");
  // The window is opened again while hz is down, as after a reboot.
  await page.reload({ waitUntil: "domcontentloaded" }).catch(() => undefined);
  const status = page.getByRole("status");
  await status.filter({ hasText: /isn't answering/ }).waitFor({ timeout: 30000 });
  const offline = await page.evaluate(() => ({
    text: document.body.innerText,
    icon: document.images[0]?.complete && document.images[0].naturalWidth > 0,
  }));
  await page.evaluate(() => {
    window.hzOfflineMarker = true; // gone once the page reloads by itself
  });
  note("the offline page is up");
  console.log("DOWN");

  await next("started");
  note("hz started again");
  // No navigation from here on: the window has to find hz on its own and open the app.
  await ready.waitFor({ timeout: 90000 });
  const reloaded = await page.evaluate(() => window.hzOfflineMarker === undefined);
  const failed = await page.getByText(/Sign in again|Open the link/).count();
  console.log(
    JSON.stringify({
      offlineShowed: /hz ui serve/.test(offline.text),
      noVersionShown: !/\d+\.\d+\.\d+/.test(offline.text),
      iconLoaded: offline.icon === true,
      openedByItself: reloaded,
      askedToSignIn: failed > 0,
    }),
  );
} finally {
  input.close();
  await context.close();
}
